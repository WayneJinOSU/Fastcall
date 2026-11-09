import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import time
import uuid
import wave
from pathlib import Path

from pipecat.frames.frames import OutputAudioRawFrame

from collection_agents.services.call_config import (
    BotV3ConfigManager,
    is_voicemail_hangup_action,
    prepare_text_for_tts,
)


from collection_agents.infra.paths import PROJECT_ROOT

REGISTRY_PATH = PROJECT_ROOT / "conf" / "cartesia_audio_assets.json"
ASSETS_ROOT = PROJECT_ROOT / "assets" / "generated_tts"

DEFAULT_CARTESIA_VOICE_ID = "71a7ad14-091c-4e8e-a314-022ece01c121"
DEFAULT_MODEL = "sonic-3.6"
DEFAULT_LOCALE = "zh-CN"
SAMPLE_RATE = 8000
AUDIO_FORMAT = "wav"
TEXT_TYPE_VOICEMAIL = "voicemail_message"
DEFAULT_FALLBACK = "live_tts"

_registry_lock = asyncio.Lock()
_logger = logging.getLogger(__name__)
_SENTENCE_BREAK_PATTERN = re.compile(r"[。.]")


def normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").strip()


def build_text_hash(text: str) -> str:
    normalized_text = normalize_text(text)
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()[:16]


def split_voicemail_segments(text: str) -> tuple[str, str]:
    """Return the live first sentence and cacheable remainder of a voicemail."""

    normalized_text = normalize_text(text)
    match = _SENTENCE_BREAK_PATTERN.search(normalized_text)
    if not match:
        return "", normalized_text

    live_text = normalized_text[: match.end()].strip()
    cached_text = normalized_text[match.end() :].strip()
    if not cached_text:
        return "", normalized_text
    return live_text, cached_text


async def ensure_voicemail_audio_asset(call_data: dict) -> dict | None:
    """
    确保当前 bot 的 voicemail 固定留言音频已存在。
    不阻塞主流程失败：失败返回 None。
    """

    request = None
    try:
        request = _build_voicemail_audio_request(call_data)
        if request is None:
            return None

        registry = _load_registry()
        existing_audio = _get_ready_robot_audio(registry, request["bot_code"])
        if existing_audio and _robot_audio_matches_request(existing_audio, request):
            _logger.info(
                "audio_asset_hit bot_code=%s text_hash=%s file_path=%s",
                request["bot_code"],
                request["text_hash"],
                existing_audio.get("file_path"),
            )
            return dict(existing_audio)

        async with _registry_lock:
            registry = _load_registry()
            existing_audio = _get_ready_robot_audio(registry, request["bot_code"])
            if existing_audio and _robot_audio_matches_request(existing_audio, request):
                _logger.info(
                    "audio_asset_hit bot_code=%s text_hash=%s file_path=%s",
                    request["bot_code"],
                    request["text_hash"],
                    existing_audio.get("file_path"),
                )
                return dict(existing_audio)

            _logger.info(
                "audio_asset_generate_start bot_code=%s text_hash=%s file_path=%s",
                request["bot_code"],
                request["text_hash"],
                request["file_path"],
            )
            audio_bytes = await _generate_cartesia_wav_bytes(
                text=request["text"],
                voice_id=request["voice_id"],
                model=request["model"],
                locale=request["locale"],
                sample_rate=request["sample_rate"],
            )
            if not audio_bytes:
                raise RuntimeError("Cartesia returned empty audio bytes")

            absolute_file_path = _resolve_audio_file_path(request["file_path"])
            absolute_file_path.parent.mkdir(parents=True, exist_ok=True)
            _write_validated_wav(
                absolute_file_path,
                audio_bytes,
                sample_rate=request["sample_rate"],
            )

            robot_audio = _build_robot_audio_record(request)
            registry.setdefault("robots", {})[request["bot_code"]] = robot_audio
            _write_registry(registry)

            _logger.info(
                "audio_asset_generate_success bot_code=%s text_hash=%s file_path=%s",
                request["bot_code"],
                request["text_hash"],
                request["file_path"],
            )
            return dict(robot_audio)
    except Exception as exc:
        _logger.exception(
            "audio_asset_generate_failed bot_code=%s text_hash=%s error=%s",
            (request or {}).get("bot_code"),
            (request or {}).get("text_hash"),
            exc,
        )
        return None


def find_audio_asset(bot_code: str) -> dict | None:
    """
    根据 bot_code 查找可播放音频。
    """

    try:
        registry = _load_registry()
        robot_audio = _get_ready_robot_audio(registry, bot_code)
        if not robot_audio:
            _logger.info(
                "audio_asset_miss bot_code=%s reason=robot_audio_missing",
                bot_code,
            )
            return None

        _logger.info(
            "audio_asset_hit bot_code=%s text_hash=%s file_path=%s",
            bot_code,
            robot_audio.get("text_hash"),
            robot_audio.get("file_path"),
        )
        return dict(robot_audio)
    except Exception as exc:
        _logger.exception(
            "audio_asset_miss bot_code=%s reason=lookup_failed error=%s",
            bot_code,
            exc,
        )
        return None


def load_audio_frame(asset: dict) -> OutputAudioRawFrame:
    """
    从 wav 文件读取音频，转为 Pipecat OutputAudioRawFrame。
    """

    file_path = _resolve_audio_file_path(asset.get("file_path", ""))
    with wave.open(str(file_path), "rb") as audio_file:
        return OutputAudioRawFrame(
            audio_file.readframes(-1),
            audio_file.getframerate(),
            audio_file.getnchannels(),
        )


def _build_voicemail_audio_request(call_data: dict) -> dict | None:
    config_manager = BotV3ConfigManager(call_data)
    config = config_manager.config if isinstance(config_manager.config, dict) else {}
    bot_code = (config.get("code") or "").strip()
    if not bot_code:
        _logger.warning("audio_asset_generate_failed bot_code missing")
        return None

    voicemail_config = config_manager.get_voicemail_config()
    if is_voicemail_hangup_action(voicemail_config):
        return None

    tts_config = config_manager.get_tts_config()
    config_variables = config.get("variables")
    config_variables = config_variables if isinstance(config_variables, dict) else {}
    # Keep the cache source text identical to bot_beta's live voicemail text.
    # Otherwise a template such as {{app_type}} either leaks to Cartesia or makes
    # the generated asset fail the runtime text-match check.
    raw_text = prepare_text_for_tts(
        voicemail_config.get("content") or "",
        tts_config,
        config_variables,
    )
    if not raw_text:
        return None

    _, text = split_voicemail_segments(raw_text)
    if not text:
        return None
    provider = (tts_config.get("provider") or "cartesia").strip().lower()
    if provider != "cartesia":
        _logger.info(
            "audio_asset_generate_failed bot_code=%s provider=%s reason=unsupported_provider",
            bot_code,
            provider,
        )
        return None

    voice_id = (tts_config.get("voice_id") or DEFAULT_CARTESIA_VOICE_ID).strip()
    model = (tts_config.get("model") or DEFAULT_MODEL).strip()
    locale = (tts_config.get("locale") or DEFAULT_LOCALE).strip()
    text_hash = build_text_hash(text)
    file_path = _build_robot_audio_file_path(
        bot_code=bot_code,
        provider=provider,
        text_hash=text_hash,
    )

    return {
        "bot_code": bot_code,
        "provider": provider,
        "voice_id": voice_id,
        "model": model,
        "locale": locale,
        "sample_rate": SAMPLE_RATE,
        "audio_format": AUDIO_FORMAT,
        "text_type": TEXT_TYPE_VOICEMAIL,
        "text_hash": text_hash,
        "text": text,
        "file_path": file_path,
    }


async def _generate_cartesia_wav_bytes(
    *,
    text: str,
    voice_id: str,
    model: str,
    locale: str,
    sample_rate: int,
) -> bytes:
    api_key = os.getenv("CARTESIA_API_KEY")
    if not api_key:
        raise RuntimeError("CARTESIA_API_KEY is not configured")

    from cartesia import AsyncCartesia

    client = AsyncCartesia(api_key=api_key)
    try:
        chunks = []
        async for chunk in client.tts.bytes(
            model_id=model,
            transcript=text,
            voice={"mode": "id", "id": voice_id},
            language=_cartesia_language(locale),
            output_format={
                "container": "wav",
                "encoding": "pcm_s16le",
                "sample_rate": sample_rate,
            },
        ):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        await _close_cartesia_client(client)


async def _close_cartesia_client(client) -> None:
    close = getattr(client, "close", None)
    if close:
        close_result = close()
        if inspect.isawaitable(close_result):
            await close_result

    client_wrapper = getattr(client, "_client_wrapper", None)
    wrapped_http_client = getattr(client_wrapper, "httpx_client", None)
    raw_http_client = getattr(wrapped_http_client, "httpx_client", None)
    aclose = getattr(raw_http_client, "aclose", None)
    if aclose:
        close_result = aclose()
        if inspect.isawaitable(close_result):
            await close_result


def _cartesia_language(locale: str) -> str:
    language = (locale or DEFAULT_LOCALE).split("-")[0].strip().lower()
    if language == "fil":
        return "tl"
    return language or DEFAULT_LOCALE.split("-")[0].lower()


def _build_robot_audio_record(request: dict) -> dict:
    return {
        "bot_code": request["bot_code"],
        "provider": request["provider"],
        "voice_id": request["voice_id"],
        "model": request["model"],
        "locale": request["locale"],
        "sample_rate": request["sample_rate"],
        "audio_format": request["audio_format"],
        "text_type": request["text_type"],
        "text_hash": request["text_hash"],
        "text": request["text"],
        "file_path": request["file_path"],
        "created_at": int(time.time() * 1000),
        "status": "ready",
        "enabled": True,
        "fallback": DEFAULT_FALLBACK,
    }


def _build_robot_audio_file_path(
    *,
    bot_code: str,
    provider: str,
    text_hash: str,
) -> str:
    file_path = (
        ASSETS_ROOT
        / _safe_path_part(provider)
        / _safe_path_part(bot_code)
        / f"{_safe_path_part(text_hash)}.{AUDIO_FORMAT}"
    )
    return _relative_to_project(file_path)


def _safe_path_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value))


def _robot_audio_matches_request(robot_audio: dict, request: dict) -> bool:
    for field in (
        "provider",
        "voice_id",
        "model",
        "locale",
        "sample_rate",
        "audio_format",
        "text_type",
        "text_hash",
        "text",
    ):
        if robot_audio.get(field) != request.get(field):
            return False
    return True


def _get_ready_robot_audio(registry: dict, bot_code: str | None) -> dict | None:
    if not bot_code:
        return None
    robot_audio = registry.get("robots", {}).get(bot_code)
    if not isinstance(robot_audio, dict):
        return None
    if not robot_audio.get("enabled", True):
        return None
    if robot_audio.get("status") != "ready":
        return None
    if robot_audio.get("audio_format") != AUDIO_FORMAT:
        return None
    file_path = _resolve_audio_file_path(robot_audio.get("file_path", ""))
    if not file_path.is_file():
        return None
    try:
        _validate_wav_file(file_path, sample_rate=robot_audio.get("sample_rate"))
    except (OSError, ValueError, wave.Error) as exc:
        _logger.warning(
            "audio_asset_miss bot_code=%s reason=invalid_wav file_path=%s error=%s",
            bot_code,
            robot_audio.get("file_path"),
            exc,
        )
        return None
    return robot_audio


def _load_registry() -> dict:
    if not REGISTRY_PATH.is_file():
        return {"robots": {}}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as registry_file:
        registry = json.load(registry_file)
    if not isinstance(registry, dict):
        return {"robots": {}}
    robots = registry.get("robots")
    if not isinstance(robots, dict):
        registry = {"robots": {}}
    return registry


def _write_registry(registry: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as registry_file:
        json.dump(registry, registry_file, ensure_ascii=False, indent=2)
        registry_file.write("\n")
    os.replace(tmp_path, REGISTRY_PATH)


def _resolve_audio_file_path(file_path: str) -> Path:
    path = Path(file_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _relative_to_project(file_path: Path) -> str:
    try:
        return str(file_path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(file_path)


def _write_validated_wav(file_path: Path, audio_bytes: bytes, *, sample_rate: int) -> None:
    temp_path = file_path.with_name(f".{file_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp_path, "wb") as audio_file:
            audio_file.write(audio_bytes)
            audio_file.flush()
            os.fsync(audio_file.fileno())
        _validate_wav_file(temp_path, sample_rate=sample_rate)
        os.replace(temp_path, file_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _validate_wav_file(file_path: Path, *, sample_rate: int | None) -> None:
    with wave.open(str(file_path), "rb") as audio_file:
        if audio_file.getcomptype() != "NONE":
            raise ValueError("WAV must use uncompressed PCM")
        if audio_file.getsampwidth() != 2:
            raise ValueError("WAV must use 16-bit PCM")
        if audio_file.getnchannels() != 1:
            raise ValueError("WAV must be mono")
        if sample_rate is not None and audio_file.getframerate() != int(sample_rate):
            raise ValueError(
                f"WAV sample rate {audio_file.getframerate()} does not match {sample_rate}"
            )
        if audio_file.getnframes() <= 0:
            raise ValueError("WAV contains no audio frames")
