import logging
import os
import re

from collection_agents.text.normalization import replace_person_name_in_text
from collection_agents.text.words_process import convert_numbers_in_text

LLM_TIMEOUT_SECS_DEFAULT = 6.0
LLM_RETRY_ON_TIMEOUT_DEFAULT = True


_FIRST_QUESTION_BY_LANGUAGE = {
    "zh": "你好",
    "en": "Hello",
    "es": "Hola",
    "tl": "Kumusta",
}


class CallConfigManager:
    """Manages bot configuration from call_data."""

    def __init__(self, call_data):
        self.call_data = call_data if isinstance(call_data, dict) else {}
        data = self.call_data.get("data", {})
        data = data if isinstance(data, dict) else {}
        root_config = data.get("config")
        fallback_config = self.call_data.get("config")
        self.config = (
            root_config if isinstance(root_config, dict) else None
        ) or (
            fallback_config if isinstance(fallback_config, dict) else None
        ) or {}

    def get_asr_config(self):
        asr = self.config.get("asr", {})
        asr = asr if isinstance(asr, dict) else {}
        provider = str(asr.get("provider", "deepgram") or "deepgram").strip().lower()
        config = {
            "provider": provider,
            "locale": asr.get("language", asr.get("locale", "zh-CN")),
            "name": asr.get("name", "中文"),
        }
        if provider == "cartesia":
            config["model"] = asr.get("model", "ink-whisper") or "ink-whisper"
        elif "model" in asr:
            config["model"] = asr["model"]
        return config

    def get_tts_config(self):
        tts = self.config.get("tts", {})
        ttsConfig = self.config.get("ttsConfig", {})
        tts = tts if isinstance(tts, dict) else {}
        ttsConfig = ttsConfig if isinstance(ttsConfig, dict) else {}
        return {
            "provider": tts.get("provider", "cartesia"),
            "voice_id": tts.get("voice_id", tts.get("name", "")),
            "model": tts.get("model", "sonic-3.6"),
            "locale": tts.get("language", tts.get("locale", "zh-CN")),
            "volume": tts.get("volume", ttsConfig.get("volume", 1.0)),
            "speed": tts.get("speed", ttsConfig.get("rate", 0.9)),
        }

    def get_llm_config(self):
        direct_llm = self.config.get("llm", {})
        if self.config.get("schema_version") == "robot-app/v1" and isinstance(direct_llm, dict):
            provider = direct_llm.get("provider", "")
            model_config = {
                "provider": provider,
                "name": direct_llm.get("model", ""),
            }
            model_config_inner = {
                "temperature": direct_llm.get("temperature"),
                "topP": direct_llm.get("top_p"),
                "maxTokens": direct_llm.get("max_tokens"),
                "llmTimeoutSecs": direct_llm.get("timeout_ms") / 1000,
            }
        else:
            model_config = self.config.get("modelConfig", {})
            model_config = model_config if isinstance(model_config, dict) else {}
            model_config_inner = model_config.get("config", {})
            provider = model_config.get("provider", "openrouter")

        if provider == "openrouter":
            default_base_url = "https://openrouter.ai/api/v1"
            default_api_key = os.getenv("OPENROUTER_API_KEY")
        elif provider == "azure":
            default_base_url = ""
            default_api_key = ""
        elif provider == "deepseek":
            default_base_url = ""
            default_api_key = os.getenv("DEEPSEEK_API_KEY")
        else:
            default_base_url = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1")
            default_api_key = os.getenv("OPENAI_API_KEY")

        llm_timeout_secs = model_config_inner.get("llmTimeoutSecs", LLM_TIMEOUT_SECS_DEFAULT)
        retry_on_timeout = model_config_inner.get("retryOnTimeout", LLM_RETRY_ON_TIMEOUT_DEFAULT)
        max_completion_tokens = model_config_inner.get("maxTokens", 512)
        temperature = model_config_inner.get("temperature", 0.2)
        top_p = model_config_inner.get("topP", 0.9)
        frequency_penalty = model_config_inner.get("frequencyPenalty", 0)
        presence_penalty = model_config_inner.get("presencePenalty", 0)
        seed = model_config_inner.get("seed", 42)

        prompt_config = self.config.get("promptConfig", {})
        prompt_config = prompt_config if isinstance(prompt_config, dict) else {}
        model_prompt_config = model_config.get("promptConfig", {})
        model_prompt_config = model_prompt_config if isinstance(model_prompt_config, dict) else {}
        prompt_content = (
            prompt_config.get("content")
            or model_prompt_config.get("content")
            or self.get_prompt_config()
            or ""
        )

        return {
            "provider": provider,
            "model": model_config.get("name", "openai/gpt-4o"),
            "base_url": default_base_url,
            "api_key": default_api_key,
            "prompt": prompt_content,
            "temperature": temperature,
            "top_p": top_p,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
            "seed": seed,
            "max_completion_tokens": max_completion_tokens,
            "llm_timeout_secs": llm_timeout_secs,
            "retry_on_timeout": retry_on_timeout,
        }

    def get_prompt_config(self):
        if self.config.get("schema_version") == "robot-app/v1":
            prompt = self.config.get("prompt", {})
            return prompt.get("system_template", "") if isinstance(prompt, dict) else ""
        prompt_config = self.config.get("promptConfig", {})
        prompt_config = prompt_config if isinstance(prompt_config, dict) else {}
        return prompt_config.get("content", "")

    def get_flow_config(self):
        if self.config.get("schema_version") == "robot-app/v1":
            flow = self.config.get("flow", {})
            return flow if isinstance(flow, dict) else {}
        flow_config = self.config.get("flowConfig", {})
        return flow_config if isinstance(flow_config, dict) else {}

    def get_welcome_config(self):
        return self.get_flow_config().get("welcomeNode", {})

    def get_end_config(self):
        return self.get_flow_config().get("endNode", {})

    def get_voicemail_config(self):
        voicemail_config = self.get_flow_config().get("voicemailNode", {})
        return voicemail_config if isinstance(voicemail_config, dict) else {}

    def get_no_response_config(self):
        return self.get_flow_config().get("noResponse", {})


def coerce_positive_int(value, default_value: int, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logging.warning(f"Invalid {field_name}={value!r}, fallback to {default_value}")
        return default_value
    if parsed <= 0:
        logging.warning(f"Non-positive {field_name}={parsed}, fallback to {default_value}")
        return default_value
    return parsed


def is_voicemail_hangup_action(voicemail_config: dict) -> bool:
    voicemail_config = voicemail_config if isinstance(voicemail_config, dict) else {}
    return (voicemail_config.get("action") or "").strip().lower() == "hangup"


def normalize_language_code(locale: str, default: str = "zh") -> str:
    lang_code = (locale or "").split("-")[0].lower()
    if not lang_code:
        return default
    if lang_code == "fil":
        return "tl"
    return lang_code


def build_first_question_content(locale: str) -> str:
    lang_code = normalize_language_code(locale, default="zh")
    return _FIRST_QUESTION_BY_LANGUAGE.get(lang_code, _FIRST_QUESTION_BY_LANGUAGE["es"])


def is_synthetic_first_question(content: str) -> bool:
    normalized = (content or "").strip()
    return normalized in set(_FIRST_QUESTION_BY_LANGUAGE.values())


def render_template_variables(template: str, variables: dict | None) -> str:
    text = template or ""
    variables = variables if isinstance(variables, dict) else {}

    def replace_match(match):
        key = match.group(1).strip()
        value = variables.get(key)
        if value is None:
            logging.warning(f"Missing variable in TTS template: {key}")
            return match.group(0)
        return str(value)

    return re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", replace_match, text)


def prepare_text_for_tts(text_template: str, tts_config: dict | None, variables: dict | None = None) -> str:
    text = render_template_variables(text_template, variables).strip()
    if not text:
        return text

    variables = variables if isinstance(variables, dict) else {}
    text = replace_person_name_in_text(text, variables.get("user_name", ""))

    tts_config = tts_config if isinstance(tts_config, dict) else {}
    target_lang = normalize_language_code(tts_config.get("locale", "zh-CN"), default="zh")
    if target_lang not in {"zh", "es", "en", "vi"}:
        return text

    try:
        return convert_numbers_in_text(text, target_lang=target_lang)
    except Exception as exc:
        logging.warning(f"Failed to convert numbers before TTS: {exc}")
        return text


def build_no_response_hangup_prompt(locale: str, hangup_message: str) -> str:
    lang_code = normalize_language_code(locale, default="es")
    message = (hangup_message or "").strip()

    if lang_code == "zh":
        message_hint = f"可参考留言内容：{message}" if message else "请给出简短催收留言。"
        return (
            "【系统最高优先级指令】\n"
            "场景：对方连续未回应，需执行最终留言并结束通话。\n"
            "行动要求：\n"
            "1. 忽略之前所有对话上下文。\n"
            f"2. 立即开始留言，{message_hint}\n"
            "3. 触发流程 : 语音信箱或助手\n"
            "4. 语言要简练。\n"
            "5. 重要：在留言内容的最后，必须且只能输出这个标签用于挂断电话：再见"
        )

    if lang_code == "en":
        message_hint = (
            f"Use this message intent: {message}"
            if message else
            "Leave a short debt-collection voicemail."
        )
        return (
            "[System Priority Instruction]\n"
            "Situation: The customer has not responded repeatedly, and this is the final voicemail before ending the call.\n"
            "Action Required:\n"
            "1. Ignore all previous user inputs or context.\n"
            f"2. Leave the voicemail immediately. {message_hint}\n"
            "3. Triggered process: Buzón de voz o asistente\n"
            "4. Be concise.\n"
            "5. CRITICAL: At the very end of your message, you MUST output exactly this token to hang up: goodbye"
        )

    if lang_code == "tl":
        message_hint = (
            f"Gamitin ang layunin ng mensaheng ito: {message}"
            if message else
            "Mag-iwan ng maikling voicemail para sa koleksyon."
        )
        return (
            "[Pinakamataas na Prayoridad na Tagubilin sa Sistema]\n"
            "Sitwasyon: Paulit-ulit na hindi sumasagot ang customer at ito na ang huling voicemail bago tapusin ang tawag.\n"
            "Kinakailangang Gawin:\n"
            "1. Huwag pansinin ang lahat ng naunang input o konteksto.\n"
            f"2. Mag-iwan agad ng voicemail. {message_hint}\n"
            "3. I-trigger ang proseso: No response hangup\n"
            "4. Panatilihing maikli.\n"
            "5. MAHALAGA: Sa pinakadulo ng iyong mensahe, dapat at tanging ang ilalabas mong token para ibaba ang tawag ay: paalam"
        )

    message_hint = (
        f"Mensaje sugerido: {message}"
        if message else
        "Deja un mensaje breve de cobranza."
    )
    return (
        "【Instruccion Prioritaria del Sistema】\n"
        "Situacion: El cliente no respondio repetidamente; este es el mensaje final antes de finalizar la llamada.\n"
        "Accion requerida:\n"
        "1. Ignora cualquier input anterior del usuario.\n"
        f"2. Deja el mensaje de voz inmediatamente. {message_hint}\n"
        "3. Activar el proceso: No response hangup\n"
        "4. Se breve.\n"
        "5. IMPORTANTE: Al finalizar tu mensaje, DEBES escribir exactamente esta etiqueta para colgar: chao"
    )


def create_system_prompt(prompt_template, tts_config, variables=None):
    return render_template_variables(prompt_template, variables)
