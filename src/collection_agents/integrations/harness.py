"""Collection-harness prompt reference client.

The prompt content is fetched for one outbound-call attempt only.  It is kept
in memory, never cached, and never persisted by this module.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from collection_agents.infra.env import mode_k8s
import httpx

if mode_k8s == "test":
    PROMPT_API_BASE_URL = os.getenv(
        "HARNESS_BASE_URL_TEST", os.getenv("HARNESS_BASE_URL", "")
    ).strip().rstrip("/")
else:
    PROMPT_API_BASE_URL = os.getenv("HARNESS_BASE_URL", "").strip().rstrip("/")

PROMPT_FETCH_TIMEOUT_SECONDS = 5.0
PROMPT_FETCH_MAX_ATTEMPTS = 3
PROMPT_LOG_PREVIEW_CHARS = 80

logger = logging.getLogger(__name__)


class CollectionHarnessPromptError(RuntimeError):
    """Raised when a harness prompt cannot be safely used for a call."""


@dataclass(frozen=True)
class CollectionHarnessPrompt:
    prompt_uuid: str
    content: str
    source: str = ""


def extract_prompt_uuid(variables: Any) -> str:
    """Read the prompt reference only from Hermes number-level variables."""
    if not isinstance(variables, dict):
        return ""
    prompt_uuid = variables.get("prompt_uuid")
    if not isinstance(prompt_uuid, str):
        return ""
    return prompt_uuid.strip()


def _prompt_url(prompt_uuid: str) -> str:
    return f"{PROMPT_API_BASE_URL}/internal/collection-agent/prompts/{prompt_uuid}"


def _prompt_log_preview(content: str) -> str:
    """Return a short, whitespace-normalized preview for diagnostics."""
    preview = " ".join(content.split())
    if len(preview) > PROMPT_LOG_PREVIEW_CHARS:
        return preview[:PROMPT_LOG_PREVIEW_CHARS] + "..."
    return preview


def _prompt_log_fields(prompt: CollectionHarnessPrompt) -> str:
    content = prompt.content
    normalized_chars = len(" ".join(content.split()))
    return (
        f"source={prompt.source or 'unknown'} "
        f"prompt_uuid={prompt.prompt_uuid} "
        f"length_chars={len(content)} "
        f"non_whitespace_chars={normalized_chars} "
        f"lines={len(content.splitlines()) or 1} "
        f"preview={_prompt_log_preview(content)!r}"
    )


def _parse_prompt_response(prompt_uuid: str, response: httpx.Response) -> CollectionHarnessPrompt:
    try:
        body = response.json()
    except ValueError as exc:
        raise CollectionHarnessPromptError(
            f"Prompt API returned invalid JSON: prompt_uuid={prompt_uuid}"
        ) from exc

    if not isinstance(body, dict):
        raise CollectionHarnessPromptError(
            f"Prompt API returned a non-object body: prompt_uuid={prompt_uuid}"
        )

    response_prompt_uuid = body.get("promptUuid")
    content = body.get("content")
    if response_prompt_uuid != prompt_uuid:
        raise CollectionHarnessPromptError(
            f"Prompt API response UUID mismatch: prompt_uuid={prompt_uuid}"
        )
    if not isinstance(content, str) or not content.strip():
        raise CollectionHarnessPromptError(
            f"Prompt API response has no usable content: prompt_uuid={prompt_uuid}"
        )

    return CollectionHarnessPrompt(
        prompt_uuid=prompt_uuid,
        content=content,
        source=str(body.get("source") or ""),
    )


async def fetch_collection_harness_prompt(
    *,
    prompt_uuid: str,
    client: httpx.AsyncClient | None = None,
) -> CollectionHarnessPrompt:
    """Fetch a prompt by reference, with short backoff for retryable failures.

    HTTP 404 and 410 are permanent failures and are not retried.
    """
    prompt_uuid = str(prompt_uuid or "").strip()
    if not prompt_uuid:
        raise CollectionHarnessPromptError("Prompt UUID is empty")
    if not PROMPT_API_BASE_URL:
        raise CollectionHarnessPromptError(
            "HARNESS_BASE_URL is not configured; harness prompt lookup unavailable"
        )

    timeout = httpx.Timeout(PROMPT_FETCH_TIMEOUT_SECONDS)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout)

    try:
        last_error: CollectionHarnessPromptError | None = None
        for attempt in range(1, PROMPT_FETCH_MAX_ATTEMPTS + 1):
            try:
                response = await client.get(_prompt_url(prompt_uuid))
            except (httpx.HTTPError, asyncio.TimeoutError) as exc:
                last_error = CollectionHarnessPromptError(
                    f"Prompt API request failed: prompt_uuid={prompt_uuid} "
                    f"attempt={attempt}/{PROMPT_FETCH_MAX_ATTEMPTS} error={type(exc).__name__}"
                )
                logger.warning(last_error)
            else:
                if 200 <= response.status_code < 300:
                    prompt = _parse_prompt_response(prompt_uuid, response)
                    logger.info(
                        "Prompt fetched for this call only: %s",
                        _prompt_log_fields(prompt),
                    )
                    return prompt

                last_error = CollectionHarnessPromptError(
                    f"Prompt API request rejected: prompt_uuid={prompt_uuid} "
                    f"status={response.status_code} attempt={attempt}/{PROMPT_FETCH_MAX_ATTEMPTS}"
                )
                if response.status_code < 500 and response.status_code != 429:
                    logger.error(last_error)
                    raise last_error
                logger.warning(last_error)

            if attempt < PROMPT_FETCH_MAX_ATTEMPTS:
                await asyncio.sleep(min(0.25 * (2 ** (attempt - 1)), 1.0))

        if last_error is not None:
            raise last_error
        raise CollectionHarnessPromptError(
            f"Prompt API request failed: prompt_uuid={prompt_uuid}"
        )
    finally:
        if owns_client:
            await client.aclose()


async def resolve_collection_harness_llm_prompt(
    *,
    outbound_source: str,
    variables: dict[str, Any] | None,
    llm_config: dict[str, Any],
    client: httpx.AsyncClient | None = None,
    fallback_prompt: str | None = None,
) -> str | None:
    """Fetch and install a harness prompt when one is configured.

    ``fallback_prompt`` is the prompt delivered in the websocket call config.
    When supplied, prompt lookup is deliberately fail-open: missing/invalid
    references and prompt API failures are logged and the fallback remains in
    ``llm_config`` so the call can continue. Without a fallback, the original
    strict behavior is preserved and lookup errors are raised to the caller.

    Returns the fetched prompt UUID, or ``None`` when the local/fallback prompt
    should be used (non-harness source, missing reference, or failed lookup).
    """
    if outbound_source != "collection_harness":
        return None

    def use_fallback(reason: str, *, prompt_uuid: str = "") -> None:
        if fallback_prompt is None:
            return
        llm_config["prompt"] = fallback_prompt
        logger.warning(
            "Using websocket prompt fallback for harness call: reason=%s "
            "prompt_uuid=%s fallback_length_chars=%s",
            reason,
            prompt_uuid or "",
            len(fallback_prompt),
        )

    if not isinstance(variables, dict) or "prompt_uuid" not in variables:
        use_fallback("prompt_uuid_missing")
        return None

    prompt_uuid = extract_prompt_uuid(variables)
    if not prompt_uuid:
        error = CollectionHarnessPromptError(
            "collection_harness variables.prompt_uuid must be a non-empty string"
        )
        if fallback_prompt is None:
            raise error
        use_fallback("prompt_uuid_invalid")
        return None

    try:
        prompt = await fetch_collection_harness_prompt(
            prompt_uuid=prompt_uuid,
            client=client,
        )
    except Exception as exc:
        if fallback_prompt is None:
            raise
        use_fallback(f"fetch_failed:{type(exc).__name__}", prompt_uuid=prompt_uuid)
        return None

    if not isinstance(prompt.content, str) or not prompt.content.strip():
        error = CollectionHarnessPromptError(
            f"Prompt API returned empty content: prompt_uuid={prompt_uuid}"
        )
        if fallback_prompt is None:
            raise error
        use_fallback("empty_content", prompt_uuid=prompt_uuid)
        return None

    llm_config["prompt"] = prompt.content
    return prompt.prompt_uuid

import datetime
import json

from collection_agents.notes.call_notes import (
    build_context_messages_with_timestamps,
    strip_harness_prompt_system_messages,
)


# =====================================================================
# 通话完成数据回调（自 outbound_callback 合并）
# =====================================================================


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
