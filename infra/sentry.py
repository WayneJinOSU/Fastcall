import logging
import os

import sentry_sdk
from dotenv import load_dotenv
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration


load_dotenv(override=True)

_SENTRY_INITIALIZED = False
_KNOWN_WEBSOCKET_NOISE_PATTERNS = (
    "websocketdisconnect",
    "application_state: websocketstate.disconnected",
    "websocketstate.disconnected",
    "exception sending data",
)


def _extract_sentry_text(event: dict) -> str:
    parts = []

    logger_name = event.get("logger")
    if logger_name:
        parts.append(str(logger_name))

    logentry = event.get("logentry") or {}
    for key in ("formatted", "message"):
        value = logentry.get(key)
        if value:
            parts.append(str(value))

    for value in event.get("exception", {}).get("values", []):
        exc_type = value.get("type")
        exc_value = value.get("value")
        if exc_type:
            parts.append(str(exc_type))
        if exc_value:
            parts.append(str(exc_value))

    return " ".join(parts).lower()


def _should_drop_sentry_event(event: dict) -> bool:
    text = _extract_sentry_text(event)
    if not text:
        return False

    if "pipecat.transports.websocket.fastapi" in text and any(
        pattern in text for pattern in _KNOWN_WEBSOCKET_NOISE_PATTERNS
    ):
        return True

    if (
        "websocketdisconnect" in text
        and "websocketstate.disconnected" in text
    ):
        return True

    return False


def _before_send(event: dict, hint: dict) -> dict | None:
    if _should_drop_sentry_event(event):
        return None
    return event


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        logging.warning(
            "Invalid %s value %r, falling back to %s",
            name,
            value,
            default,
        )
        return default


def init_sentry() -> bool:
    global _SENTRY_INITIALIZED

    if _SENTRY_INITIALIZED:
        return True

    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        logging.info("Sentry DSN is not configured, skipping Sentry initialization")
        return False

    logging_integration = LoggingIntegration(
        level=logging.INFO,
        event_level=logging.ERROR,
    )

    environment = os.environ.get("MODE")

    logging.info("Initializing Sentry environment is %s", environment)

    if environment == "prod":
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=os.getenv("SENTRY_RELEASE"),
            send_default_pii=_get_bool_env("SENTRY_SEND_DEFAULT_PII", True),
            traces_sample_rate=_get_float_env("SENTRY_TRACES_SAMPLE_RATE", 0.0),
            profiles_sample_rate=_get_float_env("SENTRY_PROFILES_SAMPLE_RATE", 0.0),
            before_send=_before_send,
            integrations=[logging_integration, FastApiIntegration()],
        )

        _SENTRY_INITIALIZED = True
        logging.info("Sentry initialized for environment=%s", environment)
        return True

    return False
