"""Collection-harness prompt reference client.

The prompt content is fetched for one outbound-call attempt only.  It is kept
in memory, never cached, and never persisted by this module.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any
from my_utils.env import mode_k8s
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
