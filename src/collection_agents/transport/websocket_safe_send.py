import inspect
import logging
from collections.abc import Awaitable, Callable
from types import MethodType
from typing import Any

from starlette.websockets import WebSocketDisconnect, WebSocketState


_KNOWN_DISCONNECT_PATTERNS = (
    "websocketdisconnect",
    "websocketstate.disconnected",
    "response already completed",
    "cannot call send once a close message has been sent",
    "unexpected asgi message 'websocket.send'",
)


def _state_name(state: Any) -> str:
    if hasattr(state, "name"):
        return str(state.name)
    return str(state)


def _get_websocket_states(websocket) -> tuple[str, str]:
    client_state = _state_name(getattr(websocket, "client_state", "unknown"))
    application_state = _state_name(getattr(websocket, "application_state", "unknown"))
    return client_state, application_state


def is_expected_disconnect_error(exc: Exception) -> bool:
    if isinstance(exc, WebSocketDisconnect):
        return True

    message = str(exc).lower()
    return any(pattern in message for pattern in _KNOWN_DISCONNECT_PATTERNS)


def is_websocket_connected(websocket) -> bool:
    client_state = getattr(websocket, "client_state", None)
    application_state = getattr(websocket, "application_state", None)

    if client_state == WebSocketState.DISCONNECTED:
        return False
    if application_state == WebSocketState.DISCONNECTED:
        return False

    return True


def _is_websocket_open_for_transport(websocket) -> bool:
    return (
        getattr(websocket, "client_state", None) == WebSocketState.CONNECTED
        and getattr(websocket, "application_state", None) == WebSocketState.CONNECTED
    )


async def _run_disconnect_callback(
    on_disconnect: Callable[[], Awaitable[Any] | Any] | None,
) -> None:
    if not on_disconnect:
        return

    result = on_disconnect()
    if inspect.isawaitable(result):
        await result


async def _notify_pipecat_transport_disconnect(client) -> None:
    on_disconnect = getattr(client, "_collection_agents_on_disconnect", None)
    if not on_disconnect or getattr(client, "_collection_agents_disconnect_notified", False):
        return

    client._collection_agents_disconnect_notified = True
    await _run_disconnect_callback(on_disconnect)


async def _guarded_pipecat_transport_send(client, data: str | bytes) -> None:
    websocket = getattr(client, "_websocket", None)
    logger = getattr(client, "_collection_agents_logger", None) or logging.getLogger(__name__)
    description = getattr(
        client,
        "_collection_agents_send_description",
        "Pipecat websocket transport send",
    )
    if websocket is None:
        return

    if getattr(client, "_closing", False) or not _is_websocket_open_for_transport(websocket):
        client._closing = True
        client_state, application_state = _get_websocket_states(websocket)
        logger.info(
            "Skip %s, socket already disconnected. "
            "client_state=%s application_state=%s",
            description,
            client_state,
            application_state,
        )
        await _notify_pipecat_transport_disconnect(client)
        return

    try:
        if isinstance(data, bytes):
            await websocket.send_bytes(data)
        else:
            await websocket.send_text(data)
    except Exception as exc:
        if is_expected_disconnect_error(exc) or not _is_websocket_open_for_transport(websocket):
            client._closing = True
            client_state, application_state = _get_websocket_states(websocket)
            logger.info(
                "%s skipped after disconnect: %s. "
                "client_state=%s application_state=%s",
                description,
                exc.__class__.__name__,
                client_state,
                application_state,
            )
            await _notify_pipecat_transport_disconnect(client)
            return

        logger.error("%s failed: %s (%s)", description, exc.__class__.__name__, exc)


def harden_pipecat_websocket_transport(
    transport,
    *,
    on_disconnect: Callable[[], Awaitable[Any] | Any] | None = None,
    logger: logging.Logger | None = None,
    description: str = "Pipecat websocket transport send",
) -> bool:
    """Guard Pipecat FastAPI websocket transport sends after client disconnects."""
    client = getattr(transport, "_client", None)
    if not client:
        (logger or logging.getLogger(__name__)).warning(
            "Unable to install Pipecat websocket send guard for %s: missing transport._client",
            description,
        )
        return False

    logger = logger or logging.getLogger(__name__)
    client._collection_agents_on_disconnect = on_disconnect
    client._collection_agents_logger = logger
    client._collection_agents_send_description = description

    if getattr(client, "_collection_agents_send_guard", False):
        return True

    client.send = MethodType(_guarded_pipecat_transport_send, client)
    client._collection_agents_send_guard = True
    logger.info("Installed Pipecat websocket send guard for %s", description)
    return True


async def safe_send_ws_message(
    send_callable: Callable[[], Awaitable[Any]],
    *,
    websocket,
    description: str,
    logger: logging.Logger | None = None,
    on_disconnect: Callable[[], Awaitable[Any] | Any] | None = None,
) -> bool:
    logger = logger or logging.getLogger(__name__)

    if not is_websocket_connected(websocket):
        client_state, application_state = _get_websocket_states(websocket)
        logger.info(
            "Skip websocket send for %s, socket already disconnected. "
            "client_state=%s application_state=%s",
            description,
            client_state,
            application_state,
        )
        await _run_disconnect_callback(on_disconnect)
        return False

    try:
        await send_callable()
        return True
    except Exception as exc:
        if is_expected_disconnect_error(exc):
            client_state, application_state = _get_websocket_states(websocket)
            logger.info(
                "Websocket send skipped for %s after disconnect: %s. "
                "client_state=%s application_state=%s",
                description,
                exc.__class__.__name__,
                client_state,
                application_state,
            )
            await _run_disconnect_callback(on_disconnect)
            return False

        logger.error("Websocket send failed for %s: %s", description, exc)
        raise


async def safe_send_text(
    websocket,
    payload: str,
    *,
    description: str,
    logger: logging.Logger | None = None,
    on_disconnect: Callable[[], Awaitable[Any] | Any] | None = None,
) -> bool:
    return await safe_send_ws_message(
        lambda: websocket.send_text(payload),
        websocket=websocket,
        description=description,
        logger=logger,
        on_disconnect=on_disconnect,
    )


async def safe_send_bytes(
    websocket,
    payload: bytes,
    *,
    description: str,
    logger: logging.Logger | None = None,
    on_disconnect: Callable[[], Awaitable[Any] | Any] | None = None,
) -> bool:
    return await safe_send_ws_message(
        lambda: websocket.send_bytes(payload),
        websocket=websocket,
        description=description,
        logger=logger,
        on_disconnect=on_disconnect,
    )


async def send_hangup_signal(websocket_client):
    """向对方发送挂机信令（action=hangup, code=480）。连接已关闭时静默跳过。"""
    import json

    try:
        hangup_message = {
            "action": "hangup",
            "data": {"code": 480},
        }
        logging.info("Sending hangup message")
        await safe_send_text(
            websocket_client,
            json.dumps(hangup_message),
            description="bot hangup signal",
            logger=logging.getLogger(__name__),
        )
    except Exception as exc:
        if "websocket.close" in str(exc) or "response already completed" in str(exc):
            logging.warning(f"Hangup message skipped, connection already closed: {exc}")
        else:
            logging.error(f"Sending hangup message error: {exc}")
