"""催收纪要分发与通话收尾落库。

- 对话提取在 collection_agents.notes.transcripts
- 意图标签分类在 collection_agents.notes.intent_labels
- 分析工作流（LangGraph / 本地引擎）在 collection_agents.notes.workflow
"""

import asyncio
import aiohttp
import datetime
import json
import logging
import os
import time
import traceback

from collection_agents.notes import OpenRouterOpenAIClient, analyze_collection_dialogs
from collection_agents.notes.intent_labels import (
    _classify_intent_label,
    _normalize_callback_speaker,
)
from collection_agents.notes.transcripts import (
    _build_context_log_params,
    build_context_messages_with_timestamps,
    extract_dialogs,
    strip_harness_prompt_system_messages,
)
from collection_agents.infra.env import mode, mode_k8s
from collection_agents.storage.db import MysqlClient


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

