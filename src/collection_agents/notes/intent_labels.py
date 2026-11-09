"""意图标签分类：通话结束后用 LLM 给对话打意图标签并核算成本。"""

import json
import logging

from collection_agents.notes.openai_client import OpenRouterOpenAIClient


INTENT_LABEL_MODEL = "deepseek-flash"
POST_PROCESS_LLM_PRICE_MULTIPLIER = 0.70
INTENT_LABEL_PRICING = {
    # The post-processing DeepSeek supplier charges 70% of the former
    # OpenRouter list price. Source prices are quoted per 1M tokens.
    "prompt_per_1k": 0.067 / 1000 * POST_PROCESS_LLM_PRICE_MULTIPLIER,
    "completion_per_1k": 0.265 / 1000 * POST_PROCESS_LLM_PRICE_MULTIPLIER,
}


def _normalize_callback_speaker(speaker):
    if speaker == "bot":
        return "bot"
    return "agent"


def _extract_intent_config(call_data):
    if not isinstance(call_data, dict):
        return {}

    data = call_data.get("data") if isinstance(call_data.get("data"), dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    intent = data.get("intent")
    if not isinstance(intent, dict):
        intent = config.get("intent")
    return intent if isinstance(intent, dict) else {}


def _build_intent_label_schema(intent_labels):
    allowed_labels = [
        str(item.get("label", "")).strip()
        for item in intent_labels
        if isinstance(item, dict) and str(item.get("label", "")).strip()
    ]
    return {
        "type": "object",
        "properties": {
            "label": {
                "type": "string",
                "enum": allowed_labels,
            }
        },
        "required": ["label"],
        "additionalProperties": False,
    }


def _filter_intent_labels(intent_labels):
    if not isinstance(intent_labels, list):
        return []
    return [
        item
        for item in intent_labels
        if isinstance(item, dict)
        and str(item.get("label", "")).strip()
        and str(item.get("label", "")).strip().upper() != "J"
    ]


def _normalize_intent_prompt_locale(call_data):
    if not isinstance(call_data, dict):
        return "en"

    data = call_data.get("data") if isinstance(call_data.get("data"), dict) else {}
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    asr = config.get("asr") if isinstance(config.get("asr"), dict) else {}
    tts = config.get("tts") if isinstance(config.get("tts"), dict) else {}

    locale = (
        asr.get("locale")
        or tts.get("locale")
        or data.get("locale")
        or "en"
    )
    lang = str(locale).split("-")[0].strip().lower()
    if lang == "fil":
        return "tl"
    if lang in {"zh", "es", "tl"}:
        return lang
    return "en"


def _render_intent_labels(intent_labels):
    return "\n".join(
        f"- {item['label']}: {item.get('intentValue', '')}"
        for item in intent_labels
        if isinstance(item, dict) and item.get("label")
    )


def _build_intent_label_prompts(intent_code, intent_labels, dialogs, prompt_locale="en"):
    labels_text = _render_intent_labels(intent_labels)
    dialogs_text = json.dumps(dialogs, ensure_ascii=False)

    if prompt_locale == "zh":
        system_prompt = (
            "你是一个电话意图分类器。"
            "请严格从候选标签中选择且只能选择一个标签，不要自创标签。"
            "注意：1. 你需要根据 dialogs 内容判断 speaker 为 user 的意图，而不是判断 assistant 的意图；"
            "2. 如果无法确定，请选择 Z；"
            "3. 要特别区分’确认身份’和’真实愿意还款’，不要把身份确认误判为还款意愿；"
            "4. 只有在确认 user 真正愿意还款时，才选择高意向标签；"
            "5. 判断标准：看 user 回复的是 assistant 的哪类问题——"
            "如果是回答’你是XXX吗’’你还在吗’等身份或在线确认问题，则不算还款意向；"
            "只有在 assistant 明确提出还款请求（如’能否今天还款’’是否安排还款’）之后，user 表达肯定，才算高意向。"
        )
        user_prompt = (
            f"intentCode: {intent_code}\n"
            f"candidateLabels:\n{labels_text}\n\n"
            f"dialogs:\n{dialogs_text}\n\n"
            "请仅按 JSON schema 返回所选标签。"
        )
        return system_prompt, user_prompt

    if prompt_locale == "es":
        system_prompt = (
            "Eres un clasificador de intención de llamadas. "
            "Debes elegir exactamente una etiqueta de las etiquetas candidatas proporcionadas. "
            "No inventes etiquetas. "
            "Nota: 1. Debes juzgar la intención del speaker con rol 'user' según el contenido de 'dialogs', no la intención de 'assistant'; "
            "2. Si no estás seguro de qué etiqueta elegir, selecciona 'Z'; "
            "3. Presta especial atención a distinguir entre confirmar identidad y mostrar una verdadera intención de pagar. No interpretes la confirmación de identidad como voluntad de pago; "
            "4. Solo selecciona alta intención cuando el 'user' confirme genuinamente que pagará; "
            "5. Criterio clave: observa a qué pregunta del 'assistant' responde el 'user'. "
            "Si el 'user' responde a preguntas de verificación de identidad (ej. '¿Estoy hablando con [nombre]?') "
            "o de presencia (ej. '¿Sigues ahí?', '¿Estás ahí?'), eso NO es intención de pago, aunque diga 'sí'. "
            "Solo cuenta como alta intención si el 'user' responde afirmativamente a una pregunta de pago "
            "(ej. '¿Podría realizar el pago hoy?', '¿Definirá el pago total o parcial?') "
            "DESPUÉS de que el 'assistant' haya explicado la deuda o los beneficios del pago."
        )
        user_prompt = (
            f"intentCode: {intent_code}\n"
            f"candidateLabels:\n{labels_text}\n\n"
            f"dialogs:\n{dialogs_text}\n\n"
            "Devuelve solo la etiqueta elegida en el formato JSON schema."
        )
        return system_prompt, user_prompt

    system_prompt = (
        "You are a call intent classifier. "
        "Choose exactly one label from the provided candidate labels. "
        "Do not invent labels. "
        "Note: 1. You should judge the intention of the 'speaker' role as 'user' based on the 'dialogs' content, rather than the intention of 'assistant'; "
        "2. If you are unsure which label to choose, select 'Z'; "
        "3. Pay attention to whether 'user' is confirming identity or genuinely willing to repay. Do not interpret the user's confirmation of identity as willingness to repay; "
        "4. Only select a high-intent label when the 'user' genuinely confirms they will repay; "
        "5. Key criterion: look at what the 'assistant' asked before the 'user' responded. "
        "If the 'user' is answering an identity question (e.g. 'Am I speaking with [name]?') "
        "or a presence check (e.g. 'Are you still there?'), that is NOT payment intent even if they say 'yes'. "
        "Only classify as high intent if the 'user' responds positively to a payment question "
        "(e.g. 'Could you make the payment today?', 'Will you arrange a payment?') "
        "AFTER the 'assistant' has explained the debt situation or payment benefits."
    )
    user_prompt = (
        f"intentCode: {intent_code}\n"
        f"candidateLabels:\n{labels_text}\n\n"
        f"dialogs:\n{dialogs_text}\n\n"
        "Return only the chosen label in JSON schema output."
    )
    return system_prompt, user_prompt


def _compute_intent_label_cost(usage):
    prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(usage.get("completion_tokens", 0) or 0)
    return round(
        prompt_tokens / 1000.0 * INTENT_LABEL_PRICING.get("prompt_per_1k", 0)
        + completion_tokens / 1000.0 * INTENT_LABEL_PRICING.get("completion_per_1k", 0),
        8,
    )


async def _classify_intent_label(call_data, dialogs):
    intent_config = _extract_intent_config(call_data)
    intent_code = str(intent_config.get("intentCode", "")).strip()
    raw_labels = intent_config.get("intentLabels")
    intent_labels = _filter_intent_labels(raw_labels)
    if not dialogs or not intent_labels:
        return "", 0.0

    try:
        client = OpenRouterOpenAIClient.from_env()
        schema = _build_intent_label_schema(intent_labels)
        prompt_locale = _normalize_intent_prompt_locale(call_data)
        system_prompt, user_prompt = _build_intent_label_prompts(
            intent_code=intent_code,
            intent_labels=intent_labels,
            dialogs=dialogs,
            prompt_locale=prompt_locale,
        )
        result, usage = await client.generate_json_with_usage(
            model=INTENT_LABEL_MODEL,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            temperature=0,
        )
        label = str(result.get("label", "")).strip()
        allowed_labels = {str(item.get("label", "")).strip() for item in intent_labels}
        if label not in allowed_labels:
            logging.warning("Intent classification returned unsupported label: %s", label)
            return "", 0.0
        return label, _compute_intent_label_cost(usage)
    except Exception:
        logging.exception("Intent classification failed")
        return "", 0.0

