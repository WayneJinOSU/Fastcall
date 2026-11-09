import asyncio
import aiohttp
import datetime
import json
import logging
import os
import time
import traceback

from collection_agents.notes import OpenRouterOpenAIClient, analyze_collection_dialogs
from collection_agents.services.call_config import is_synthetic_first_question
from collection_agents.infra.env import mode, mode_k8s
from collection_agents.storage.db import MysqlClient


INTENT_LABEL_MODEL = "deepseek-flash"
POST_PROCESS_LLM_PRICE_MULTIPLIER = 0.70
INTENT_LABEL_PRICING = {
    # The post-processing DeepSeek supplier charges 70% of the former
    # OpenRouter list price. Source prices are quoted per 1M tokens.
    "prompt_per_1k": 0.067 / 1000 * POST_PROCESS_LLM_PRICE_MULTIPLIER,
    "completion_per_1k": 0.265 / 1000 * POST_PROCESS_LLM_PRICE_MULTIPLIER,
}


def _parse_timestamp(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_message_timestamp_ms(message, message_index, context_timestamps=None):
    if isinstance(message, dict):
        ts = _parse_timestamp(message.get("timestamp"))
        if ts is not None:
            return ts

    if isinstance(context_timestamps, list) and 0 <= message_index < len(context_timestamps):
        ts = _parse_timestamp(context_timestamps[message_index])
        if ts is not None:
            return ts

    return int(time.time() * 1000 + message_index)


def build_context_messages_with_timestamps(context):
    messages = getattr(context, "messages", None) if context else None
    if not isinstance(messages, list) and context:
        try:
            if hasattr(context, "get_messages"):
                messages = context.get_messages()
            elif hasattr(context, "get_messages_for_persistent_storage"):
                messages = context.get_messages_for_persistent_storage()
        except Exception as exc:
            logging.error(f"Error reading context messages: {exc}")
            messages = []
    if not isinstance(messages, list):
        return []

    context_timestamps = getattr(context, "_message_timestamps_ms", [])
    context_timestamps = context_timestamps if isinstance(context_timestamps, list) else []

    enriched_messages = []
    for idx, message in enumerate(messages):
        if isinstance(message, dict):
            message_copy = dict(message)
            message_copy.setdefault(
                "timestamp",
                _resolve_message_timestamp_ms(message_copy, idx, context_timestamps=context_timestamps),
            )
            enriched_messages.append(message_copy)
        else:
            enriched_messages.append(message)

    return enriched_messages


def strip_harness_prompt_system_messages(messages, metrics_collector=None):
    """Omit the one-attempt harness prompt from persisted conversation data."""
    if isinstance(metrics_collector, dict):
        prompt_uuid = metrics_collector.get("prompt_uuid", "")
    else:
        prompt_uuid = getattr(metrics_collector, "prompt_uuid", "")
    if not prompt_uuid:
        return messages
    return [
        message
        for message in messages
        if not (isinstance(message, dict) and message.get("role") == "system")
    ]


def _first_non_empty(*values, default=""):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
            continue
        return value
    return default


def _build_context_log_params(
    context_messages,
    call_uuid=None,
    conversation_meta=None,
):
    conversation_meta = conversation_meta if isinstance(conversation_meta, dict) else {}

    return {
        "user_name": _first_non_empty(
            conversation_meta.get("user_name"),
        ),
        "business_id": _first_non_empty(
            conversation_meta.get("business_id"),
            conversation_meta.get("businessID"),
        ),
        "remaining_amount": _first_non_empty(
            conversation_meta.get("remaining_amount"),
            default=0,
        ),
        "context": json.dumps(context_messages, ensure_ascii=False),
        "app_type": _first_non_empty(
            conversation_meta.get("app_type"),
        ),
        "country": _first_non_empty(
            conversation_meta.get("country"),
        ),
        "ctime1": int(time.time() * 1000),
        "uuid": call_uuid,
    }


def extract_dialogs(context, user_speaker="user", skip_prompt_prefix=False):
    dialogs = []
    messages = getattr(context, "messages", None)
    if not isinstance(messages, list):
        return dialogs
    context_timestamps = getattr(context, "_message_timestamps_ms", [])
    context_timestamps = context_timestamps if isinstance(context_timestamps, list) else []

    for idx, msg in enumerate(messages[1:]):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ["user", "assistant"]:
            continue

        speaker = user_speaker if role == "user" else "bot"
        content = msg.get("content", "")
        message_index = idx + 1
        timestamp_ms = _resolve_message_timestamp_ms(
            msg,
            message_index,
            context_timestamps=context_timestamps,
        )

        if idx in (0, 1):
            if speaker == user_speaker and is_synthetic_first_question(content):
                continue
            if speaker == "bot" and content:
                dialogs.append(
                    {
                        "speaker": speaker,
                        "content": content,
                        "timestamp": timestamp_ms,
                    }
                )
                continue

        content = content.replace("LLMFullResponseEndFrame", "")
        if content and (not skip_prompt_prefix or not content.startswith("请说：")):
            dialogs.append(
                {
                    "speaker": speaker,
                    "content": content,
                    "timestamp": timestamp_ms,
                }
            )

    return dialogs


def _extract_note_request_data(call_data):
    if not isinstance(call_data, dict):
        return {}, {}

    if isinstance(call_data.get("data"), dict):
        config_data = call_data.get("data", {}) or {}
        variables = config_data.get("config", {}).get("variables", {}) or {}
        return config_data, variables

    if isinstance(call_data.get("session_create"), dict):
        session_create = call_data.get("session_create", {}) or {}
        variables = session_create.get("config", {}).get("variables", {}) or {}
        return session_create, variables

    return {}, {}


def _extract_note_request_meta(call_data):
    config_data, variables = _extract_note_request_data(call_data)

    is_copilot = isinstance(call_data, dict) and isinstance(call_data.get("session_create"), dict)
    tenant_id_default = "100035" if is_copilot else ""
    call_uuid = ""
    if is_copilot:
        call_uuid = str(call_data.get("call_uuid", "")).strip()
    else:
        call_uuid = str(config_data.get("callUUID", "")).strip()

    return {
        "config_data": config_data,
        "variables": variables,
        "tenant_id": variables.get("tenant_id", tenant_id_default),
        "work_order_id": variables.get("work_order_id", ""),
        "country": variables.get("country", "mexico"),
        "contact_time": variables.get("contact_time", ""),
        "call_uuid": call_uuid,
        "touch_way": variables.get("touchWay", "ai_agent" if not is_copilot else "ai_cooperation_task_rotation"),
    }


def _resolve_collection_note_engine() -> str:
    engine = os.getenv("COLLECTION_NOTE_ENGINE", "post_process").strip().lower()
    if engine in {"post_process", "local", "langgraph_openai", "langgraph_openrouter"}:
        return "post_process"
    logging.warning(
        "COLLECTION_NOTE_ENGINE=%r is no longer used for post-processing; using post_process gateway",
        engine,
    )
    return "post_process"


async def _apply_local_collection_analysis(dialogs, language, final_payload, country="mexico", engine=None):
    try:
        llm_client = OpenRouterOpenAIClient.from_env()
        logging.info(
            "phone call reminder local analysis start: engine=%s country=%s language=%s dialogs_count=%d",
            engine,
            country,
            language,
            len(dialogs) if isinstance(dialogs, list) else -1,
        )
        logging.info(f"phone call reminder llm_client: {llm_client}")
        logging.info(
            "phone call reminder llm_client type: %s",
            type(llm_client).__name__ if llm_client else None,
        )
        result = await analyze_collection_dialogs(
            dialogs=dialogs,
            language=language,
            country=country,
            llm_client=llm_client,
        )
    except Exception as exc:
        logging.exception(f"phone call reminder analysis failed: {exc}")
        logging.exception("Local collection note analysis failed; keeping existing payload unchanged.")
        return False

    logging.info(f"collection note analysis result: {result}")
    final_payload.update(result)
    return True


async def _post_json_callback(callback_url, payload, headers):
    if not callback_url:
        logging.info("Note callback URL not configured (NOTE_CALLBACK_BASE_URL); skipping callback")
        return
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(callback_url, json=payload, headers=headers) as response:
                response_text = await response.text()
                if response.status == 200:
                    logging.info(f"Dialog callback sent with {payload} successfully. Response: {response_text}")
                else:
                    logging.error(f"Callback API Failed: {response.status} - {response_text}")
                    logging.error(f"Payload sent was: {payload}")
    except Exception as exc:
        logging.error(f"Error connecting to callback API: {exc}")


def _build_note_callback_url(path):
    # Optional note-callback integration. Configure NOTE_CALLBACK_BASE_URL
    # (or NOTE_CALLBACK_BASE_URL_TEST for MODE=test); unset disables callbacks.
    if mode_k8s == "test":
        base_url = os.getenv("NOTE_CALLBACK_BASE_URL_TEST", "").strip()
    else:
        base_url = os.getenv("NOTE_CALLBACK_BASE_URL", "").strip()
    if not base_url:
        return ""
    base_url = base_url.rstrip("/")
    logging.info(f"Building callback URL: {base_url}{path}")
    return f"{base_url}{path}"


async def _send_intelligent_note(
    context,
    call_data,
    language,
    default_payload,
    callback_path,
    note_type,
    skip_prompt_prefix=False,
):
    request_meta = _extract_note_request_meta(call_data)
    tenant_id = request_meta["tenant_id"]
    work_order_id = request_meta["work_order_id"]
    country = request_meta["country"]
    call_uuid = request_meta["call_uuid"]
    contact_time = request_meta["contact_time"]

    headers = {
        "Content-Type": "application/json",
        "tenant-id": tenant_id,
    }
    callback_url = _build_note_callback_url(callback_path)

    dialogs = extract_dialogs(context, user_speaker="user", skip_prompt_prefix=skip_prompt_prefix)
    final_payload = {
        **default_payload,
        "workOrderId": work_order_id,
        "contactTime": contact_time,
        "externalCallUuid": call_uuid,
    }
    if "touchWay" in final_payload:
        final_payload["touchWay"] = request_meta["touch_way"]

    engine = _resolve_collection_note_engine()
    logging.info(f"{note_type} engine selected: {engine}")

    if note_type == "collection_notes":
        local_success = await _apply_local_collection_analysis(
            dialogs=dialogs,
            language=language,
            final_payload=final_payload,
            country=country,
            engine=engine,
        )
        if not local_success:
            logging.warning("Local collection workflow failed; using default collection note payload.")

    await _post_json_callback(callback_url=callback_url, payload=final_payload, headers=headers)
    return final_payload


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


def _build_call_results_costs(metrics_collector, intent_label_cost):
    if not metrics_collector:
        return {
            "asrCost": 0.0,
            "mainLlmCost": 0.0,
            "ttsCost": 0.0,
            "intentLabelCost": round(intent_label_cost, 8),
        }
    return {
        "asrCost": round(getattr(metrics_collector, "asr_cost", 0.0) or 0.0, 8),
        "mainLlmCost": round(getattr(metrics_collector, "llm_cost", 0.0) or 0.0, 8),
        "ttsCost": round(getattr(metrics_collector, "tts_cost", 0.0) or 0.0, 8),
        "intentLabelCost": round(intent_label_cost, 8),
    }


async def _build_call_results_payload(
    call_uuid,
    context,
    call_data=None,
    metrics_collector=None,
    intent_label_override=None,
):
    dialogs = extract_dialogs(context, user_speaker="customer", skip_prompt_prefix=True)
    normalized_dialogs = [
        {
            **dialog,
            "speaker": _normalize_callback_speaker(dialog.get("speaker")),
        }
        for dialog in dialogs
    ]
    has_customer_dialog = any(
        dialog.get("speaker") == "agent" and str(dialog.get("content", "")).strip()
        for dialog in normalized_dialogs
    )

    override_label = str(intent_label_override or "").strip()
    if override_label:
        if metrics_collector and hasattr(metrics_collector, "set_intent_label"):
            metrics_collector.set_intent_label(override_label)
        return {
            "callUUID": call_uuid,
            "dialogs": normalized_dialogs,
            "intentLabel": override_label,
            "costs": _build_call_results_costs(metrics_collector, 0.0),
        }

    if not has_customer_dialog:
        if metrics_collector and hasattr(metrics_collector, "set_intent_label"):
            metrics_collector.set_intent_label("Z")
        return {
            "callUUID": call_uuid,
            "dialogs": normalized_dialogs,
            "intentLabel": "Z",
            "costs": _build_call_results_costs(metrics_collector, 0.0),
        }

    intent_dialogs = [
        {**d, "speaker": "user" if d.get("speaker") == "agent" else "assistant"}
        for d in normalized_dialogs
    ]
    intent_label, intent_label_cost = await _classify_intent_label(call_data, intent_dialogs)
    if metrics_collector and hasattr(metrics_collector, "set_intent_label"):
        metrics_collector.set_intent_label(intent_label)
    return {
        "callUUID": call_uuid,
        "dialogs": normalized_dialogs,
        "intentLabel": intent_label,
        "costs": _build_call_results_costs(metrics_collector, intent_label_cost),
    }


async def send_call_results_callback(
    call_uuid,
    context,
    call_data=None,
    metrics_collector=None,
    intent_label_override=None,
):
    try:
        # Optional call-results callback. Configure CALL_RESULTS_CALLBACK_URL
        # (or CALL_RESULTS_CALLBACK_URL_TEST for MODE=test); unset disables it.
        if mode_k8s == "test":
            callback_url = os.getenv("CALL_RESULTS_CALLBACK_URL_TEST", "").strip()
        else:
            callback_url = os.getenv("CALL_RESULTS_CALLBACK_URL", "").strip()
        if not callback_url:
            logging.info("Call-results callback URL not configured; skipping callback")
            return

        payload = await _build_call_results_payload(
            call_uuid,
            context,
            call_data=call_data,
            metrics_collector=metrics_collector,
            intent_label_override=intent_label_override,
        )
        if not payload.get("dialogs") and not payload.get("intentLabel"):
            logging.info("No dialogs to send for call-results callback")
            return

        headers = {"Content-Type": "application/json"}

        logging.info(f"Sending call-results callback to {callback_url} with payload: {payload}")
        payload_str = json.dumps(payload, ensure_ascii=False)
        logging.info(f"Call-results payload: {payload_str}")

        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session:
            async with session.post(callback_url, json=payload, headers=headers, timeout=timeout) as response:
                if response.status == 200:
                    logging.info("Call-results callback sent successfully")
                else:
                    response_text = await response.text()
                    logging.error(f"Call-results callback failed: {response.status} - {response_text}")
    except asyncio.TimeoutError:
        logging.error("Call-results callback timeout")
    except Exception as exc:
        logging.error(f"Error sending call-results callback: {exc}")
        logging.error(traceback.format_exc())


async def intelligent_notes_distribution(context, call_data, locale):
    language = locale.split("-")[0].lower()
    if language == "fil":
        language = "tl"
    if language == "en":
        language = "English"
    elif language == "es":
        language = "Spanish"
    elif language == "tl":
        language = "Tagalog"
    elif language == "vi":
        language = "Vietnamese"
    else:
        language = "Spanish"

    _, variables = _extract_note_request_data(call_data)
    if not isinstance(variables, dict):
        variables = {}
    task_type = variables.get("task_type")
    if task_type == "marketing":
        return None, task_type
    note_result = await intelligent_collection_notes(context, call_data, language=language)
    return note_result, task_type


async def intelligent_collection_notes(context, call_data, language="Spanish"):
    return await _send_intelligent_note(
        context=context,
        call_data=call_data,
        language=language,
        default_payload={
            "repaymentWish": "no",
            "collectionResult": "no_other",
            "promiseRepayment": False,
            "promiseRepaymentTime": "",
            "repeatBorrowingWish": "low",
            "remark": "",
            "contactTarget": 1,
            "contactWay": "phone",
            "contactSuccess": True,
            "touchWay": "ai_agent",
        },
        callback_path="/internal/collection/note/createByFollower",
        note_type="collection_notes",
        skip_prompt_prefix=True,
    )


async def save_conversation(
    context,
    call_uuid=None,
    session=None,
    conversation_meta=None,
    call_data=None,
    metrics_collector=None,
    intent_label_override=None,
):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    filename = f"conversation_{timestamp}.json"
    logging.info(f"writing conversation to {filename}")
    if conversation_meta:
        logging.info(f"conversation_meta: {conversation_meta}")

    if call_uuid:
        await send_call_results_callback(
            call_uuid,
            context,
            call_data=call_data,
            metrics_collector=metrics_collector,
            intent_label_override=intent_label_override,
        )

    context_messages_with_ts = strip_harness_prompt_system_messages(
        build_context_messages_with_timestamps(context),
        metrics_collector,
    )
    logging.info(f"conversation_context: {json.dumps(context_messages_with_ts, indent=4, ensure_ascii=False)}")

    if mode != "local":
        params = _build_context_log_params(
            context_messages_with_ts,
            call_uuid=call_uuid,
            conversation_meta=conversation_meta,
        )
        insert_sql = """
            INSERT INTO context_log
            (user_name, business_id, remaining_amount, context, app_type, country, ctime1, uuid)
            VALUES
            (:user_name, :business_id, :remaining_amount, :context, :app_type, :country, :ctime1, :uuid)
        """
        try:
            MysqlClient.execute(insert_sql, params)
        except Exception as exc:
            logging.exception("Failed to save conversation context_log: %s", exc)
        return

    try:
        with open(filename, "w") as file:
            json.dump(context_messages_with_ts, file, indent=2, ensure_ascii=False)
    except Exception as exc:
        logging.error(f"Error in save_conversation: {exc}")


async def _test_openrouter_langgraph_real_api_collection_analysis():
    client = OpenRouterOpenAIClient.from_env()
    result = await analyze_collection_dialogs(
        dialogs=[
            {
                "speaker": "bot",
                "content": "Hola, llamo sobre tu préstamo vencido. ¿Puedes pagar hoy?",
            },
            {
                "speaker": "user",
                "content": "this is 1, 4, 8 ,7",
            },
        ],
        language="Spanish",
        country="mexico",
        now=datetime.datetime(2026, 5, 19, 10, 0, 0),
        llm_client=client,
    )
    print(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))



if __name__ == "__main__":
    asyncio.run(_test_openrouter_langgraph_real_api_collection_analysis())
