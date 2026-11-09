import logging
import os

from deepgram import LiveOptions
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService

from collection_agents.bots.copilot.config import (
    LLM_RETRY_ON_TIMEOUT_DEFAULT,
    LLM_TIMEOUT_SECS_DEFAULT,
)
from collection_agents.services.providers import select_azure_llm_target


def create_stt_service(asr_config):
    provider = asr_config.get("provider", "deepgram")
    locale = asr_config.get("locale", "zh-CN")
    model = asr_config.get("model", "nova-2")

    if provider == "deepgram":
        return DeepgramSTTService(
            api_key=os.environ.get("DEEPGRAM_API_KEY"),
            live_options=LiveOptions(
                encoding="linear16",
                language=locale,
                model=model,
                sample_rate=8000,
                channels=1,
                interim_results=True,
                smart_format=True,
            )
        )
    else:
        logging.warning(
            f"Unknown STT provider: {provider}, using deepgram"
        )
        return create_stt_service({
            "provider": "deepgram",
            "locale": locale
        })


def create_tts_service(tts_config):
    provider = tts_config.get("provider", "cartesia")
    voice_id = tts_config.get("voice_id", "")
    model = tts_config.get("model", "sonic-3.6")
    locale = tts_config.get("locale", "zh-CN")

    if provider == "cartesia":
        language = locale.split("-")[0]
        generation_config_kwargs = {}
        volume = tts_config.get("volume")
        speed = tts_config.get("speed")
        if volume is not None:
            generation_config_kwargs["volume"] = round(0.5 + float(volume) / 100.0 * 1.5, 4)
        if speed is not None:
            generation_config_kwargs["speed"] = round(0.6 + (float(speed) - 0.5) / 1.5 * 0.9, 4)
        generation_config = (
            GenerationConfig(**generation_config_kwargs)
            if generation_config_kwargs
            else None
        )
        params = CartesiaTTSService.InputParams(
            language=language,
            generation_config=generation_config,
        )
        default_voice = "e50405f9-347e-4a1d-b5b6-943bbdb30cd5"
        model_name = (
            model if model.startswith("sonic")
            else "sonic-multilingual"
        )
        return CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY"),
            voice_id=voice_id or default_voice,
            model=model_name,
            params=params,
            sample_rate=8000,
        )
    else:
        logging.warning(
            f"Unknown TTS provider: {provider}, using cartesia"
        )
        return create_tts_service({
            "provider": "cartesia",
            "voice_id": voice_id,
            "model": model,
            "locale": locale,
            "volume": tts_config.get("volume"),
            "speed": tts_config.get("speed"),
        })


def create_llm_service(llm_config):
    model = llm_config.get("model", "openai/gpt-4o")
    base_url = llm_config.get("base_url", "https://openrouter.ai/api/v1")
    api_key = llm_config.get("api_key") or os.getenv("OPENROUTER_API_KEY")
    temperature = llm_config.get("temperature", 0.2)
    top_p = llm_config.get("top_p", 0.9)
    frequency_penalty = llm_config.get("frequency_penalty", 0)
    presence_penalty = llm_config.get("presence_penalty", 0)
    seed = llm_config.get("seed", 42)
    max_completion_tokens = llm_config.get("max_completion_tokens", 256)
    llm_timeout_secs = llm_config.get("llm_timeout_secs", LLM_TIMEOUT_SECS_DEFAULT)
    retry_on_timeout = llm_config.get("retry_on_timeout", LLM_RETRY_ON_TIMEOUT_DEFAULT)

    input_params_kwargs = {
        "temperature": temperature,
        "top_p": top_p,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
        "seed": seed,
        "max_completion_tokens": max_completion_tokens,
    }

    if llm_config.get("provider") in {"openrouter", "azure"}:
        target = select_azure_llm_target()
        return AzureLLMService(
            api_key=target["api_key"],
            endpoint=target["endpoint"],
            api_version=target["api_version"],
            model=target["deployment"],
            retry_timeout_secs=llm_timeout_secs,
            retry_on_timeout=retry_on_timeout,
            params=OpenAILLMService.InputParams(**input_params_kwargs),
        )

    return OpenAILLMService(
        api_key=api_key,
        model=model,
        base_url=base_url,
        retry_timeout_secs=llm_timeout_secs,
        retry_on_timeout=retry_on_timeout,
        params=OpenAILLMService.InputParams(**input_params_kwargs),
    )
