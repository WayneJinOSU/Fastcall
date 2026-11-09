"""Outbound callback for completed intelligent-collection call data."""

import asyncio
import datetime
import json
import logging
import os
from typing import Any

import httpx

from bot_beta_modules.notes import (
    build_context_messages_with_timestamps,
    strip_harness_prompt_system_messages,
)
from my_utils.env import mode_k8s


LOGGER = logging.getLogger(__name__)

# Intelligent-collection callback configuration. Optional integration: configure
# HARNESS_CALLBACK_URL (or HARNESS_CALLBACK_URL_TEST for MODE=test); when unset the
# callback is skipped.
CALLBACK_URL_TEST = os.getenv(
    "HARNESS_CALLBACK_URL_TEST", os.getenv("HARNESS_CALLBACK_URL", "")
).strip()
CALLBACK_URL_PROD = os.getenv("HARNESS_CALLBACK_URL", "").strip()
CALLBACK_TIMEOUT_SECONDS = 10.0
CALLBACK_MAX_ATTEMPTS = 3
PROMPT_PARAMETER_KEYS = (
    "temperature",
    "top_p",
    "top_k",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "max_completion_tokens",
    "max_tokens",
    "repetition_penalty",
)


def _resolve_callback_url() -> str:
    """Select the Harness callback endpoint from the Kubernetes MODE."""
    return CALLBACK_URL_TEST if mode_k8s == "test" else CALLBACK_URL_PROD


def _json_safe(value: Any) -> Any:
    """Return a structure that httpx can always JSON-encode."""
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=lambda item: item.isoformat()
            if isinstance(item, (datetime.datetime, datetime.date))
            else str(item),
        )
    )


def build_call_data_callback_payload(
    *,
    context: Any = None,
    metrics_collector: Any = None,
    collection_note: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the versioned payload sent after a call finishes."""
    metrics = metrics_collector.to_dict() if metrics_collector else {}
    dialogs = build_context_messages_with_timestamps(context) if context else []
    dialogs = strip_harness_prompt_system_messages(dialogs, metrics)
    call_uuid = str(metrics.get("call_uuid") or "")
    call_metrics = _sanitize_call_metrics_for_callback(metrics)

    payload = {
        "schemaVersion": "1.0",
        "eventType": "call.completed",
        # "occurredAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "callUuid": call_uuid,
        "businessId": metrics.get("business_id") or "",
        "workOrderId": metrics.get("work_order_id") or "",
        "outboundSource": metrics.get("outbound_source") or "hermes",
        "call": call_metrics,
        "dialogs": dialogs,
        "prompt": _build_prompt_callback_value(metrics),
        "collectionNote": collection_note or {},
    }
    return _json_safe(payload)


def _build_prompt_callback_value(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return a prompt reference for harness calls, or the legacy content."""
    prompt_value: dict[str, Any] = {
        "model": metrics.get("llm_model_name") or "",
    }

    prompt_uuid = metrics.get("prompt_uuid")
    if prompt_uuid:
        prompt_value["promptUuid"] = prompt_uuid
    else:
        prompt_value["content"] = metrics.get("llm_prompt") or ""

    llm_param = metrics.get("llm_param")
    if isinstance(llm_param, dict):
        prompt_value["parameters"] = {
            key: llm_param[key]
            for key in PROMPT_PARAMETER_KEYS
            if key in llm_param
        }
    else:
        prompt_value["parameters"] = {}
    return prompt_value


def _sanitize_call_metrics_for_callback(metrics: dict[str, Any]) -> dict[str, Any]:
    """Remove prompt text and credentials from the nested call metrics."""
    sanitized = dict(metrics)
    sanitized.pop("llm_prompt", None)

    llm_param = sanitized.get("llm_param")
    if isinstance(llm_param, dict):
        sanitized["llm_param"] = {
            key: llm_param[key]
            for key in PROMPT_PARAMETER_KEYS
            if key in llm_param
        }
    else:
        sanitized["llm_param"] = {}
    return sanitized


async def send_call_data_callback(
    *,
    context: Any = None,
    metrics_collector: Any = None,
    collection_note: dict[str, Any] | None = None,
) -> bool:
    """POST completed call data for collection-harness calls only.

    Delivery failures are non-fatal so this callback can never prevent local
    call data from being finalized and persisted.
    """
    try:
        payload = build_call_data_callback_payload(
            context=context,
            metrics_collector=metrics_collector,
            collection_note=collection_note,
        )
    except Exception:
        LOGGER.exception("Failed to build call data callback payload")
        return False
    call_uuid = payload.get("callUuid", "")
    call_metrics = payload.get("call")
    outbound_source = (
        call_metrics.get("outbound_source", "hermes")
        if isinstance(call_metrics, dict)
        else "hermes"
    )
    if outbound_source != "collection_harness":
        LOGGER.debug(
            "Call data callback skipped: call_uuid=%s outbound_source=%s",
            call_uuid,
            outbound_source,
        )
        return False

    callback_url = _resolve_callback_url()
    if not callback_url:
        LOGGER.info(
            "Call data callback skipped: HARNESS_CALLBACK_URL not configured (call_uuid=%s)",
            call_uuid,
        )
        return False

    headers = {
        "Content-Type": "application/json",
        "X-Callback-Event": "call.completed",
        "X-Call-UUID": call_uuid,
    }

    timeout = httpx.Timeout(CALLBACK_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(1, CALLBACK_MAX_ATTEMPTS + 1):
                try:
                    response = await client.post(
                        callback_url,
                        json=payload,
                        headers=headers,
                    )
                    response_text = response.text[:500]
                    if 200 <= response.status_code < 300:
                        LOGGER.info(
                            "Call data callback delivered: call_uuid=%s status=%s attempt=%s",
                            call_uuid,
                            response.status_code,
                            attempt,
                        )
                        return True

                    LOGGER.warning(
                        "Call data callback rejected: call_uuid=%s status=%s "
                        "attempt=%s response=%r",
                        call_uuid,
                        response.status_code,
                        attempt,
                        response_text,
                    )
                    if response.status_code < 500:
                        return False
                except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                    LOGGER.warning(
                        "Call data callback request failed: call_uuid=%s attempt=%s/%s error=%s",
                        call_uuid,
                        attempt,
                        CALLBACK_MAX_ATTEMPTS,
                        exc,
                    )

                if attempt < CALLBACK_MAX_ATTEMPTS:
                    await asyncio.sleep(min(2 ** (attempt - 1), 5))
    except Exception:
        LOGGER.exception("Unexpected call data callback error: call_uuid=%s", call_uuid)

    return False
