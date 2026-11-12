import logging
import os

from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.openai.llm import OpenAILLMService

from collection_agents.bots.copilot.config import (
    LLM_RETRY_ON_TIMEOUT_DEFAULT,
    LLM_TIMEOUT_SECS_DEFAULT,
)
from collection_agents.services.providers import (
    STT_SERVICE_BUILDERS,
    TTS_SERVICE_BUILDERS,
    create_stt_service as create_provider_stt_service,
    create_tts_service as create_provider_tts_service,
    select_azure_llm_target,
)


def create_stt_service(asr_config):
    config = dict(asr_config)
    provider = str(config.get("provider", "deepgram") or "deepgram").strip().lower()
    if provider not in STT_SERVICE_BUILDERS:
        logging.warning(f"Unknown STT provider: {provider}, using deepgram")
        provider = "deepgram"
        config["provider"] = provider

    if provider == "deepgram":
        provider_config = dict(config.get("provider_config") or {})
        params = dict(provider_config.get("params") or {})
        params.setdefault("smart_format", True)
        provider_config["params"] = params
        config["provider_config"] = provider_config

    config["provider"] = provider
    return create_provider_stt_service(config)


def create_tts_service(tts_config):
    config = dict(tts_config)
    provider = str(config.get("provider", "cartesia") or "cartesia").strip().lower()
    if provider not in TTS_SERVICE_BUILDERS:
        logging.warning(f"Unknown TTS provider: {provider}, using cartesia")
        provider = "cartesia"
        config["provider"] = provider

    if provider == "cartesia":
        model = str(config.get("model") or "sonic-3.6")
        config["model"] = model if model.startswith("sonic") else "sonic-multilingual"
        config["voice_id"] = (
            config.get("voice_id") or "e50405f9-347e-4a1d-b5b6-943bbdb30cd5"
        )

    config["provider"] = provider
    return create_provider_tts_service(config)


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
