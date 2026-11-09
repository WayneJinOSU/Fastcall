import asyncio
import json
import logging
import os
import wave
from types import MethodType

from openai import APITimeoutError
from deepgram import LiveOptions
from pipecat.frames.frames import OutputAudioRawFrame
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.qwen.llm import QwenLLMService
from pipecat.services.cartesia.stt import CartesiaLiveOptions, CartesiaSTTService
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.stt import CommitStrategy, ElevenLabsRealtimeSTTService as _ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.elevenlabs import tts as _elevenlabs_tts_module
from pipecat.transcriptions.language import Language
from bot_beta_modules.vosk_stt import VoskSTTService

_elevenlabs_tts_module.ELEVENLABS_MULTILINGUAL_MODELS.add("eleven_v3_conversational")


class ElevenLabsRealtimeSTTService(_ElevenLabsRealtimeSTTService):
    async def _start_metrics(self):
        await self.start_ttfb_metrics()
        await self.start_processing_metrics()

    async def _on_partial_transcript(self, data: dict):
        await self.stop_ttfb_metrics()
        await super()._on_partial_transcript(data)
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.openai.llm import OpenAILLMService

from bot_beta_modules.config import (
    LLM_RETRY_ON_TIMEOUT_DEFAULT,
    LLM_TIMEOUT_SECS_DEFAULT,
    coerce_positive_int,
)
from my_utils.websocket_safe_send import safe_send_text


ALI_QWEN_PROVIDER = "ali_qwen"
# Private OpenAI-compatible endpoint for Ali Qwen agents. Must be supplied via
# environment; no default is shipped.
ALI_QWEN_BASE_URL = os.getenv("ALI_QWEN_BASE_URL", "").strip()
ALI_QWEN_MODEL = os.getenv("ALI_QWEN_MODEL", "d-1_model")


def _load_routing_config():
    """Load robot-code routing lists from conf/routing.json (path overridable
    via ROUTING_CONFIG_PATH). Missing/invalid file yields empty routing, which
    keeps every robot on the default providers."""
    path = os.getenv(
        "ROUTING_CONFIG_PATH",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "conf", "routing.json"),
    )
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        logging.info("Routing config not found at %s; using empty routing", path)
        return {}
    except Exception as exc:
        logging.warning("Routing config at %s unreadable (%s); using empty routing", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


_ROUTING = _load_routing_config()

DEEPSEEK_ROBOT_CODES = frozenset(_ROUTING.get("deepseek_robot_codes") or [])
VOSK_FORCED_ROBOT_CODES = frozenset(_ROUTING.get("vosk_forced_robot_codes") or [])
_DEEPGRAM_NOVA3_ROBOT_CODES = frozenset(_ROUTING.get("deepgram_nova3_robot_codes") or [])


def resolve_deepgram_model(robot_code):
    """Return the Deepgram model tier for a robot (single source of truth for
    the nova-3/nova-2 split, previously duplicated in three places)."""
    return "nova-3" if robot_code in _DEEPGRAM_NOVA3_ROBOT_CODES else "nova-2"


def is_ali_qwen_agent(agent_name):
    """Return whether an agent name should use the private Ali Qwen service."""
    return "qwen" in str(agent_name or "").casefold()


def resolve_bot_llm_runtime(llm_config, agent_name, robot_code):
    """Resolve bot_beta's effective LLM config and database platform value."""
    resolved = dict(llm_config or {})

    # Agent-name routing has the highest priority: Qwen agents must never fall
    # through to the Azure/DeepSeek routes used by other agents.
    if is_ali_qwen_agent(agent_name):
        resolved.pop("api_key", None)
        resolved.pop("max_completion_tokens", None)
        resolved.update(
            {
                "provider": ALI_QWEN_PROVIDER,
                "model": ALI_QWEN_MODEL,
                "base_url": ALI_QWEN_BASE_URL,
                "temperature": 0.9,
                "top_p": 0.95,
                "top_k": 40,
                "repetition_penalty": 1.15,
                "max_tokens": 128,
            }
        )
        return resolved, "gpu"

    if robot_code in DEEPSEEK_ROBOT_CODES:
        resolved.update(
            {
                "provider": "deepseek",
                "model": "deepseek-flash",
            }
        )

    return resolved, "CPU"


def select_azure_llm_target():
    """Load the single Azure OpenAI target from the environment.

    Kept as the single Azure platform capability: one endpoint, no pool.
    Required env vars: AZURE_LLM_ENDPOINT / AZURE_LLM_API_KEY /
    AZURE_LLM_DEPLOYMENT / AZURE_LLM_API_VERSION.
    """
    endpoint = os.getenv("AZURE_LLM_ENDPOINT", "").strip()
    api_key = os.getenv("AZURE_LLM_API_KEY", "").strip()
    deployment = os.getenv("AZURE_LLM_DEPLOYMENT", "").strip()
    api_version = os.getenv("AZURE_LLM_API_VERSION", "").strip()
    values = {
        "AZURE_LLM_ENDPOINT": endpoint,
        "AZURE_LLM_API_KEY": api_key,
        "AZURE_LLM_DEPLOYMENT": deployment,
        "AZURE_LLM_API_VERSION": api_version,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(
            "Azure LLM target is incomplete; missing env vars: " + ", ".join(missing)
        )
    if "****" in values.values():
        raise ValueError("Azure LLM target still contains placeholder values")
    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "deployment": deployment,
        "api_version": api_version,
    }


def resolve_stt_provider(asr_config, robot_code):
    """Return the effective STT provider for this call.

    An explicit asr_config["provider"] always wins so a robot can be rolled
    back to deepgram (or pushed to vosk) from the platform config without a
    release. The robot_code fallback below only applies while the config
    carries no provider of its own.
    """
    if robot_code in VOSK_FORCED_ROBOT_CODES:
        return "vosk"
    else:
        configured = str(asr_config.get("provider") or "").strip().lower()
    if configured:
        return configured
    return "deepgram"


def create_stt_service(asr_config, robot_code, robot_name, grammar=None):
    # provider = "elevenlabs"
    provider = resolve_stt_provider(asr_config, robot_code)
    
    locale = asr_config.get("locale", "zh-CN")

    if provider == "cartesia":
        api_key = os.getenv("CARTESIA_API_KEY", "").strip()
        if not api_key:
            raise ValueError("CARTESIA_API_KEY is required for Cartesia STT")

        language = _cartesia_stt_language(locale)
        sample_rate = 8000
        return CartesiaSTTService(
            api_key=api_key,
            sample_rate=sample_rate,
            live_options=CartesiaLiveOptions(
                model=asr_config.get("model", "ink-whisper"),
                language=language,
                encoding="pcm_s16le",
                sample_rate=sample_rate,
            ),
        )

    if provider == "elevenlabs":
        logging.info("Using ElevenLabsRealtimeSTTService")
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for ElevenLabs STT")

        language_code = _elevenlabs_stt_language(locale)
        return ElevenLabsRealtimeSTTService(
            api_key=api_key,
            sample_rate=8000,
            params=ElevenLabsRealtimeSTTService.InputParams(
                language_code=language_code,
                commit_strategy=CommitStrategy.VAD,
                vad_silence_threshold_secs=0.3,
                vad_threshold=0.6,
            ),
        )

    if provider == "vosk":
        model_path = asr_config.get("model_path", "/mnt/data/vosk-model-small-es-0.42")
        return VoskSTTService(model_path=model_path, sample_rate=8000, grammar=grammar)

    if provider == "deepgram":
        return DeepgramSTTService(
            api_key=os.environ.get("DEEPGRAM_API_KEY"),
            live_options=LiveOptions(
                encoding="linear16",
                language=_deepgram_stt_language(locale=locale),
                model=resolve_deepgram_model(robot_code),
                sample_rate=8000,
                channels=1,
                interim_results=True,
                endpointing=100,
                smart_format=False,
            ),
        )

    logging.warning(f"Unknown STT provider: {provider}, using deepgram")
    return create_stt_service(
        {"provider": "deepgram", "locale": locale},
        robot_code,
        robot_name,
    )


def resolve_deepgram_shadow_config(asr_config, robot_code):
    """Config for the Deepgram shadow decoder used to A/B against live Vosk.

    Mirrors exactly what create_stt_service would build for a Deepgram call on
    this robot (same model tier + language) so the comparison is apples-to-apples.
    Returns None when the shadow cannot run (no API key / SDK missing), letting
    the caller skip wiring entirely.

    Scope gate: the shadow runs ONLY for robots in VOSK_FORCED_ROBOT_CODES.
    Any other robot (including one that opts into Vosk via asr_config
    provider="vosk") gets None so no Deepgram call/cost is incurred.
    """
    from bot_beta_modules.shadow_stt import _DEEPGRAM_AVAILABLE

    if robot_code not in VOSK_FORCED_ROBOT_CODES:
        return None
    if not _DEEPGRAM_AVAILABLE:
        return None
    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        return None
    locale = asr_config.get("locale", "zh-CN")
    return {
        "api_key": api_key,
        "language": _deepgram_stt_language(locale=locale),
        "model": resolve_deepgram_model(robot_code),
        "sample_rate": 8000,
    }


_CARTESIA_LOCALE_MAP = {"fil": "tl"}


def _deepgram_stt_language(locale):
    """Return a Deepgram-compatible language code for the configured locale."""
    normalized = str(locale or "en").strip().replace("_", "-")
    language = normalized.split("-", 1)[0].lower()
    if language == "vi":
        return "vi"
    return normalized or "en"


def _cartesia_language(locale):
    """Convert application locale codes to Cartesia language codes."""
    normalized = str(locale or "en").strip().replace("_", "-").lower()
    language = normalized.split("-", 1)[0] or "en"
    return _CARTESIA_LOCALE_MAP.get(language, language)


def _cartesia_stt_language(locale):
    """Convert application locales (for example zh-CN) to Cartesia language codes."""
    return _cartesia_language(locale)


_ELEVENLABS_LOCALE_MAP = {
    "zh": "zho",
    "en": "eng",
    "es": "spa",
    "pt": "por",
    "fr": "fra",
    "de": "deu",
    "ja": "jpn",
    "ko": "kor",
    "id": "ind",
    "vi": "vie",
    "th": "tha",
    "ar": "ara",
    "ru": "rus",
    "hi": "hin",
    "fil": "fil",
}


def _elevenlabs_stt_language(locale):
    normalized = str(locale or "en").strip().replace("_", "-").lower()
    language = normalized.split("-", 1)[0]
    return _ELEVENLABS_LOCALE_MAP.get(language, language)


def create_tts_service(tts_config, session):
    # provider = "elevenlabs"
    provider = tts_config.get("provider", "cartesia")
    voice_id = tts_config.get("voice_id", "")
    model = tts_config.get("model", "sonic-3.6")
    locale = tts_config.get("locale", "zh-CN")

    if provider == "cartesia":
        generation_config_kwargs = {}
        volume = tts_config.get("volume")
        speed = tts_config.get("speed")
        if volume is not None:
            # backend [0, 100] → cartesia [0.5, 2.0]
            generation_config_kwargs["volume"] = round(0.5 + float(volume) / 100.0 * 1.5, 4)
        if speed is not None:
            # backend [0.5, 2] → cartesia [0.6, 1.5]
            generation_config_kwargs["speed"] = round(0.6 + (float(speed) - 0.5) / 1.5 * 0.9, 4)
        generation_config = (
            GenerationConfig(**generation_config_kwargs)
            if generation_config_kwargs
            else None
        )
        parameters = CartesiaTTSService.InputParams(
            language=_cartesia_language(locale),
            generation_config=generation_config,
        )
        return CartesiaTTSService(
            model=model,
            api_key=os.getenv("CARTESIA_API_KEY"),
            voice_id=voice_id or "71a7ad14-091c-4e8e-a314-022ece01c121",
            sample_rate=8000,
            params=parameters,
        )

    if provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for ElevenLabs TTS")
        language = _elevenlabs_tts_language(locale)
        return ElevenLabsTTSService(
            api_key=api_key,
            voice_id=tts_config.get("voice_id") or os.getenv("ELEVENLABS_VOICE_ID", ""),
            model=tts_config.get("model") or "eleven_v3_conversational",
            sample_rate=8000,
            params=ElevenLabsTTSService.InputParams(language=language),
        )

    raise ValueError(f"Unsupported TTS provider: {provider}")


def _elevenlabs_tts_language(locale) -> Language:
    normalized = str(locale or "en").strip().replace("_", "-").lower()
    tag = normalized.split("-", 1)[0]
    mapping = {
        "zh": Language.ZH, "en": Language.EN, "es": Language.ES,
        "pt": Language.PT, "fr": Language.FR, "de": Language.DE,
        "ja": Language.JA, "ko": Language.KO, "id": Language.ID,
        "vi": Language.VI, "th": Language.TH, "ar": Language.AR,
        "ru": Language.RU, "hi": Language.HI,
    }
    return mapping.get(tag, Language.EN)


def create_llm_service(llm_config):
    model = llm_config.get("model", "openai/gpt-4o")
    provider = llm_config.get("provider")

    if provider == ALI_QWEN_PROVIDER:
        api_key = os.environ.get("ALI_QWEN_API_KEY", "").strip()
        if not api_key:
            raise ValueError("ALI_QWEN_API_KEY is required for Ali Qwen agents")
        base_url = llm_config.get("base_url") or ALI_QWEN_BASE_URL
        if not base_url:
            raise ValueError("ALI_QWEN_BASE_URL is required for Ali Qwen agents")

        # OpenAILLMService always requests stream=True. Provider-specific fields
        # are sent via extra_body so the OpenAI SDK includes them in the JSON body.
        qwen_params = OpenAILLMService.InputParams(
            temperature=llm_config.get("temperature", 0.9),
            top_p=llm_config.get("top_p", 0.95),
            max_tokens=coerce_positive_int(
                llm_config.get("max_tokens", 128),
                default_value=128,
                field_name="max_tokens",
            ),
            extra={
                "extra_body": {
                    "top_k": llm_config.get("top_k", 40),
                    "repetition_penalty": llm_config.get(
                        "repetition_penalty",
                        1.15,
                    ),
                    "chat_template_kwargs": {
                        "enable_thinking": False,
                    },
                }
            },
        )
        return OpenAILLMService(
            api_key=api_key,
            model=model,
            base_url=base_url,
            retry_timeout_secs=llm_config.get("llm_timeout_secs", LLM_TIMEOUT_SECS_DEFAULT),
            retry_on_timeout=llm_config.get("retry_on_timeout", LLM_RETRY_ON_TIMEOUT_DEFAULT),
            params=qwen_params,
        )

    max_completion_tokens = coerce_positive_int(
        llm_config.get("max_completion_tokens", 512),
        default_value=128,
        field_name="max_completion_tokens",
    )
    input_params_kwargs = {
        "max_completion_tokens": max_completion_tokens,
        "temperature": llm_config.get("temperature", 0.2),
        "top_p": llm_config.get("top_p", 0.9),
        "frequency_penalty": llm_config.get("frequency_penalty", 0.15),
        "presence_penalty": llm_config.get("presence_penalty", -0.1),
        "seed": llm_config.get("seed", 42),
    }
    if llm_config.get("service_tier"):
        input_params_kwargs["service_tier"] = llm_config["service_tier"]
    if llm_config.get("extra"):
        input_params_kwargs["extra"] = llm_config["extra"]


    if provider == "azure":
        # Azure OpenAI with weighted failover across AZURE_LLM_{1..8}_* env targets.
        target = select_azure_llm_target()
        return AzureLLMService(
            api_key=target["api_key"],
            endpoint=target["endpoint"],
            api_version=target["api_version"],
            model=target["deployment"],
            retry_timeout_secs=llm_config.get("llm_timeout_secs", LLM_TIMEOUT_SECS_DEFAULT),
            retry_on_timeout=llm_config.get("retry_on_timeout", LLM_RETRY_ON_TIMEOUT_DEFAULT),
            params=OpenAILLMService.InputParams(**input_params_kwargs),
        )

    if provider == "deepseek":
        # DeepSeek 不支持 seed 和 max_completion_tokens，其 _build_chat_completion_params
        # 已剔除这两个字段并改用 max_tokens，其余参数与 OpenAI 兼容。
        deepseek_params = {k: v for k, v in input_params_kwargs.items()
                          if k not in {"seed", "service_tier"}}
        # 关闭思考模式：thinking 必须放在 extra_body 里，否则 OpenAI SDK 不认识该字段。
        # extra 里的 key 会被 _build_chat_completion_params 通过 params.update() 展开，
        # 最终以 create(extra_body={...}) kwarg 形式传给 SDK。
        extra = dict(deepseek_params.get("extra") or {})
        extra_body = dict(extra.pop("extra_body", {}) or {})
        extra_body.setdefault("thinking", {"type": "disabled"})
        extra_body["user_id"] = "collection_agents"
        extra["extra_body"] = extra_body
        deepseek_params["extra"] = extra
        return DeepSeekLLMService(
            api_key=llm_config.get("api_key") or os.environ.get("DEEPSEEK_API_KEY"),
            model=llm_config.get("model") or "deepseek-chat",
            retry_timeout_secs=llm_config.get("llm_timeout_secs", LLM_TIMEOUT_SECS_DEFAULT),
            retry_on_timeout=llm_config.get("retry_on_timeout", LLM_RETRY_ON_TIMEOUT_DEFAULT),
            params=OpenAILLMService.InputParams(**deepseek_params),
        )

    if provider == "openrouter":
        return OpenAILLMService(
            api_key=llm_config.get("api_key") or os.environ.get("OPENROUTER_API_KEY"),
            model=model,
            base_url=llm_config.get("base_url") or "https://openrouter.ai/api/v1",
            retry_timeout_secs=llm_config.get("llm_timeout_secs", LLM_TIMEOUT_SECS_DEFAULT),
            retry_on_timeout=llm_config.get("retry_on_timeout", LLM_RETRY_ON_TIMEOUT_DEFAULT),
            params=OpenAILLMService.InputParams(**input_params_kwargs),
        )

    # Default: any OpenAI-compatible endpoint (OpenAI, vLLM, Ollama, ...).
    # Self-hosted servers usually accept any non-empty key, hence the "EMPTY"
    # fallback so a fully local stack works without a real API key.
    return OpenAILLMService(
        api_key=llm_config.get("api_key") or os.environ.get("OPENAI_API_KEY") or "EMPTY",
        model=model,
        base_url=(
            llm_config.get("base_url")
            or os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("OPENAI_API_URL")
            or "http://localhost:8000/v1"
        ),
        retry_timeout_secs=llm_config.get("llm_timeout_secs", LLM_TIMEOUT_SECS_DEFAULT),
        retry_on_timeout=llm_config.get("retry_on_timeout", LLM_RETRY_ON_TIMEOUT_DEFAULT),
        params=OpenAILLMService.InputParams(**input_params_kwargs),
    )


def attach_llm_retry_counter(llm_service, metrics_collector):
    if not getattr(llm_service, "_retry_on_timeout", False):
        return llm_service

    original_get_chat_completions = getattr(llm_service, "get_chat_completions", None)
    if original_get_chat_completions is None or getattr(original_get_chat_completions, "_retry_counter_patched", False):
        return llm_service

    async def _tracked_get_chat_completions(self, params_from_context):
        params = self.build_chat_completion_params(params_from_context)

        if self._retry_on_timeout:
            try:
                return await asyncio.wait_for(
                    self._client.chat.completions.create(**params), timeout=self._retry_timeout_secs
                )
            except (APITimeoutError, asyncio.TimeoutError):
                metrics_collector.increment_llm_retry()
                logging.info("LLM retry counted after timeout")
                return await self._client.chat.completions.create(**params)

        return await self._client.chat.completions.create(**params)

    _tracked_get_chat_completions._retry_counter_patched = True
    llm_service.get_chat_completions = MethodType(_tracked_get_chat_completions, llm_service)
    return llm_service


def load_output_sounds(script_dir, sound_files=None):
    sounds = {}
    for file_name in (sound_files or ["incoming.wav", "incoming4.wav", "14287.wav"]):
        full_path = os.path.join(script_dir, "assets", file_name)
        with wave.open(full_path) as audio_file:
            sounds[file_name] = OutputAudioRawFrame(
                audio_file.readframes(-1),
                audio_file.getframerate(),
                audio_file.getnchannels(),
            )
    return sounds


async def send_hangup_signal(websocket_client):
    try:
        hangup_message = {
            "action": "hangup",
            "data": {"code": 480},
        }
        logging.info("Sending hangup message")
        await safe_send_text(
            websocket_client,
            json.dumps(hangup_message),
            description="bot hangup signal",
            logger=logging.getLogger(__name__),
        )
    except Exception as exc:
        if "websocket.close" in str(exc) or "response already completed" in str(exc):
            logging.warning(f"Hangup message skipped, connection already closed: {exc}")
        else:
            logging.error(f"Sending hangup message error: {exc}")
