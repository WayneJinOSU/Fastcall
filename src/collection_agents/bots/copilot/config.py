import os
from typing import Any

LLM_TIMEOUT_SECS_DEFAULT = 6.0
LLM_MAX_RETRIES_DEFAULT = 3
LLM_RETRY_ON_TIMEOUT_DEFAULT = True


class CopilotConfigManager:
    """Manages bot configuration from session_create data."""

    def __init__(self, session_create: dict[str, Any]):
        self.config = session_create.get("config", {})

    def get_asr_config(self):
        asr = self.config.get("asr", {})
        return {
            "code": asr.get("code", ""),
            "provider": asr.get("provider", "deepgram"),
            "locale": asr.get("locale", "zh-CN"),
            "name": asr.get("name", "中文"),
            "model": asr.get("model", "nova-2"),
        }

    def get_tts_config(self):
        tts = self.config.get("tts", {})
        ttsConfig = self.config.get("ttsConfig", {})
        tts = tts if isinstance(tts, dict) else {}
        ttsConfig = ttsConfig if isinstance(ttsConfig, dict) else {}
        return {
            "provider": tts.get("provider", "cartesia"),
            "voice_id": tts.get("name", ""),
            "model": tts.get("model", "sonic-3.6"),
            "locale": tts.get("locale", "zh-CN"),
            "volume": ttsConfig.get("volume", 1.0),
            "speed": ttsConfig.get("rate", 0.9),
        }

    def get_llm_config(self):
        model_config = self.config.get("modelConfig", {})
        config = model_config.get("config", {}) if isinstance(model_config.get("config"), dict) else {}
        provider = model_config.get("provider", "openrouter")

        if provider == "openrouter":
            default_base_url = "https://openrouter.ai/api/v1"
            default_api_key = os.getenv("OPENROUTER_API_KEY")
        elif provider == "azure":
            default_base_url = ""
            default_api_key = ""
        else:
            default_base_url = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1")
            default_api_key = os.getenv("OPENAI_API_KEY")

        llm_timeout_secs = config.get("llmTimeoutSecs", LLM_TIMEOUT_SECS_DEFAULT)
        max_retries = config.get("maxRetries", LLM_MAX_RETRIES_DEFAULT)
        retry_on_timeout = config.get("retryOnTimeout", LLM_RETRY_ON_TIMEOUT_DEFAULT)
        max_completion_tokens = config.get("maxTokens", 256)
        temperature = config.get("temperature", 0.2)
        top_p = config.get("topP", 0.9)
        frequency_penalty = config.get("frequencyPenalty", 0)
        presence_penalty = config.get("presencePenalty", 0)
        seed = config.get("seed", 42)

        return {
            "provider": provider,
            "model": model_config.get("name", "openai/gpt-4o"),
            "base_url": model_config.get("url") or default_base_url,
            "api_key": model_config.get("apiKey") or default_api_key,
            "temperature": temperature,
            "top_p": top_p,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
            "seed": seed,
            "max_completion_tokens": max_completion_tokens,
            "llm_timeout_secs": llm_timeout_secs,
            "max_retries": max_retries,
            "retry_on_timeout": retry_on_timeout,
        }

    def get_prompt_config(self):
        prompt_config = self.config.get("promptConfig", {})
        return prompt_config.get("content", "")

    def get_flow_config(self):
        return self.config.get("flowConfig", {})
