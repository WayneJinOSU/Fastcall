import asyncio
import logging
import os
from types import MethodType

from openai import APITimeoutError
from deepgram import LiveOptions
from pipecat.services.assemblyai.models import AssemblyAIConnectionParams
from pipecat.services.assemblyai.stt import AssemblyAISTTService
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.qwen.llm import QwenLLMService
from pipecat.services.cartesia.stt import CartesiaLiveOptions, CartesiaSTTService
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.stt import CommitStrategy, ElevenLabsRealtimeSTTService as _ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.soniox.stt import SonioxInputParams, SonioxSTTService
from pipecat.services.speechmatics.stt import SpeechmaticsSTTService
from pipecat.transcriptions.language import Language
from collection_agents.services.stt_local import VoskSTTService


class ElevenLabsRealtimeSTTService(_ElevenLabsRealtimeSTTService):
    async def _start_metrics(self):
        await self.start_ttfb_metrics()
        await self.start_processing_metrics()

    async def _on_partial_transcript(self, data: dict):
        await self.stop_ttfb_metrics()
        await super()._on_partial_transcript(data)
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.openai.llm import OpenAILLMService

from collection_agents.services.call_config import (
    LLM_RETRY_ON_TIMEOUT_DEFAULT,
    LLM_TIMEOUT_SECS_DEFAULT,
    coerce_positive_int,
    get_asr_fallback_model,
    get_tts_fallback_model,
    normalize_asr_provider,
    normalize_tts_provider,
)


ALI_QWEN_PROVIDER = "ali_qwen"
# Private OpenAI-compatible endpoint for Ali Qwen agents. Must be supplied via
# environment; no default is shipped.
ALI_QWEN_BASE_URL = os.getenv("ALI_QWEN_BASE_URL", "").strip()
ALI_QWEN_MODEL = os.getenv("ALI_QWEN_MODEL", "d-1_model")


def is_ali_qwen_agent(agent_name):
    """Return whether an agent name should use the private Ali Qwen service."""
    return "qwen" in str(agent_name or "").casefold()


def resolve_bot_llm_runtime(llm_config, agent_name, robot_code):
    """Resolve standard's effective LLM config and database platform value."""
    resolved = dict(llm_config or {})

    # Agent-name routing has the highest priority for private Qwen agents.
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
    return normalize_asr_provider(asr_config.get("provider"))


def _asr_provider_config(asr_config):
    provider_config = asr_config.get("provider_config", {})
    return dict(provider_config) if isinstance(provider_config, dict) else {}


def _asr_provider_params(asr_config):
    params = _asr_provider_config(asr_config).get("params", {})
    return dict(params) if isinstance(params, dict) else {}


def _stt_language(locale):
    normalized = str(locale or "en").strip().replace("_", "-")
    for candidate in (normalized, normalized.split("-", 1)[0].lower()):
        try:
            return Language(candidate)
        except ValueError:
            continue
    raise ValueError(f"Unsupported STT language: {locale}")


def _create_cartesia_stt_service(asr_config, robot_code, robot_name, grammar):
    api_key = os.getenv("CARTESIA_API_KEY", "").strip()
    if not api_key:
        raise ValueError("CARTESIA_API_KEY is required for Cartesia STT")
    provider = resolve_stt_provider(asr_config, robot_code)
    sample_rate = 8000
    return CartesiaSTTService(
        api_key=api_key,
        sample_rate=sample_rate,
        live_options=CartesiaLiveOptions(
            model=asr_config.get("model") or get_asr_fallback_model(provider),
            language=_cartesia_stt_language(asr_config.get("locale", "zh-CN")),
            encoding="pcm_s16le",
            sample_rate=sample_rate,
        ),
    )


def _create_elevenlabs_stt_service(asr_config, robot_code, robot_name, grammar):
    logging.info("Using ElevenLabsRealtimeSTTService")
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY is required for ElevenLabs STT")
    language_code = _elevenlabs_stt_language(asr_config.get("locale", "zh-CN"))
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


def _create_vosk_stt_service(asr_config, robot_code, robot_name, grammar):
    model_path = asr_config.get("model_path", "/mnt/data/vosk-model-small-es-0.42")
    return VoskSTTService(model_path=model_path, sample_rate=8000, grammar=grammar)


def _create_deepgram_stt_service(asr_config, robot_code, robot_name, grammar):
    provider = resolve_stt_provider(asr_config, robot_code)
    live_options = _asr_provider_params(asr_config)
    live_options.update(
        {
            "encoding": "linear16",
            "language": _deepgram_stt_language(asr_config.get("locale", "zh-CN")),
            "model": asr_config.get("model") or get_asr_fallback_model(provider),
            "sample_rate": 8000,
            "channels": 1,
            "interim_results": True,
        }
    )
    live_options.setdefault("endpointing", 100)
    live_options.setdefault("smart_format", False)
    return DeepgramSTTService(
        api_key=os.environ.get("DEEPGRAM_API_KEY"),
        live_options=LiveOptions(**live_options),
    )


def _create_assemblyai_stt_service(asr_config, robot_code, robot_name, grammar):
    api_key = os.getenv("ASSEMBLYAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ASSEMBLYAI_API_KEY is required for AssemblyAI STT")
    provider = resolve_stt_provider(asr_config, robot_code)
    connection_params = _asr_provider_params(asr_config)
    connection_params.setdefault("sample_rate", 8000)
    connection_params.setdefault("encoding", "pcm_s16le")
    connection_params["speech_model"] = (
        asr_config.get("model") or get_asr_fallback_model(provider)
    )
    provider_config = _asr_provider_config(asr_config)
    service_options = {
        key: provider_config[key]
        for key in (
            "api_endpoint_base_url",
            "vad_force_turn_endpoint",
            "should_interrupt",
            "speaker_format",
        )
        if key in provider_config
    }
    return AssemblyAISTTService(
        api_key=api_key,
        language=_stt_language(asr_config.get("locale", "zh-CN")),
        connection_params=AssemblyAIConnectionParams(**connection_params),
        **service_options,
    )


def _create_speechmatics_stt_service(asr_config, robot_code, robot_name, grammar):
    api_key = os.getenv("SPEECHMATICS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("SPEECHMATICS_API_KEY is required for Speechmatics STT")
    provider = resolve_stt_provider(asr_config, robot_code)
    params = _asr_provider_params(asr_config)
    params["language"] = _stt_language(asr_config.get("locale", "zh-CN"))
    params["operating_point"] = asr_config.get("model") or get_asr_fallback_model(provider)
    params.setdefault("audio_encoding", "pcm_s16le")
    provider_config = _asr_provider_config(asr_config)
    service_options = {
        key: provider_config[key]
        for key in ("base_url", "should_interrupt")
        if key in provider_config
    }
    return SpeechmaticsSTTService(
        api_key=api_key,
        sample_rate=8000,
        params=SpeechmaticsSTTService.InputParams(**params),
        **service_options,
    )


def _create_soniox_stt_service(asr_config, robot_code, robot_name, grammar):
    api_key = os.getenv("SONIOX_API_KEY", "").strip()
    if not api_key:
        raise ValueError("SONIOX_API_KEY is required for Soniox STT")
    provider = resolve_stt_provider(asr_config, robot_code)
    params = _asr_provider_params(asr_config)
    params["model"] = asr_config.get("model") or get_asr_fallback_model(provider)
    params.setdefault("audio_format", "pcm_s16le")
    params.setdefault("num_channels", 1)
    params.setdefault(
        "language_hints",
        [_stt_language(asr_config.get("locale", "zh-CN"))],
    )
    provider_config = _asr_provider_config(asr_config)
    service_options = {
        key: provider_config[key]
        for key in ("url", "vad_force_turn_endpoint")
        if key in provider_config
    }
    return SonioxSTTService(
        api_key=api_key,
        sample_rate=8000,
        params=SonioxInputParams(**params),
        **service_options,
    )


STT_SERVICE_BUILDERS = {
    "deepgram": _create_deepgram_stt_service,
    "cartesia": _create_cartesia_stt_service,
    "elevenlabs": _create_elevenlabs_stt_service,
    "assemblyai": _create_assemblyai_stt_service,
    "speechmatics": _create_speechmatics_stt_service,
    "soniox": _create_soniox_stt_service,
    "vosk": _create_vosk_stt_service,
}


def create_stt_service(asr_config, robot_code="", robot_name="", grammar=None):
    provider = resolve_stt_provider(asr_config, robot_code)
    builder = STT_SERVICE_BUILDERS.get(provider)
    if builder is None:
        logging.warning(f"Unknown STT provider: {provider}, using deepgram")
        asr_config = {
            "provider": "deepgram",
            "locale": asr_config.get("locale", "zh-CN"),
        }
        builder = STT_SERVICE_BUILDERS["deepgram"]
    return builder(asr_config, robot_code, robot_name, grammar)


def resolve_deepgram_shadow_config(asr_config, robot_code):
    """Build the opt-in Deepgram shadow configuration for a Vosk call."""
    from collection_agents.services.stt_local import _DEEPGRAM_AVAILABLE

    if not asr_config.get("deepgram_shadow", False) or not _DEEPGRAM_AVAILABLE:
        return None
    api_key = os.environ.get("DEEPGRAM_API_KEY")
    if not api_key:
        return None
    locale = asr_config.get("locale", "zh-CN")
    return {
        "api_key": api_key,
        "language": _deepgram_stt_language(locale=locale),
        "model": asr_config.get("model") or "nova-2",
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


def _create_cartesia_tts_service(tts_config):
    provider = normalize_tts_provider(tts_config.get("provider"))
    voice_id = tts_config.get("voice_id", "")
    locale = tts_config.get("locale", "zh-CN")
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
        model=tts_config.get("model") or get_tts_fallback_model(provider),
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id=voice_id or "71a7ad14-091c-4e8e-a314-022ece01c121",
        sample_rate=8000,
        params=parameters,
    )


def _create_elevenlabs_tts_service(tts_config):
    provider = normalize_tts_provider(tts_config.get("provider"))
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ELEVENLABS_API_KEY is required for ElevenLabs TTS")
    voice_id = str(
        tts_config.get("voice_id") or os.getenv("ELEVENLABS_VOICE_ID", "")
    ).strip()
    if not voice_id:
        raise ValueError("ElevenLabs TTS requires tts.voice_id or ELEVENLABS_VOICE_ID")
    speed = tts_config.get("speed")
    if speed is not None:
        speed = float(speed)
        if not 0.7 <= speed <= 1.2:
            raise ValueError("ElevenLabs TTS speed must be between 0.7 and 1.2")
    language = _elevenlabs_tts_language(tts_config.get("locale", "zh-CN"))
    return ElevenLabsTTSService(
        api_key=api_key,
        voice_id=voice_id,
        model=tts_config.get("model") or get_tts_fallback_model(provider),
        sample_rate=8000,
        params=ElevenLabsTTSService.InputParams(language=language, speed=speed),
    )


TTS_SERVICE_BUILDERS = {
    "cartesia": _create_cartesia_tts_service,
    "elevenlabs": _create_elevenlabs_tts_service,
}


def create_tts_service(tts_config, session=None):
    provider = normalize_tts_provider(tts_config.get("provider"))
    builder = TTS_SERVICE_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(f"Unsupported TTS provider: {provider}")
    return builder(tts_config)


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

