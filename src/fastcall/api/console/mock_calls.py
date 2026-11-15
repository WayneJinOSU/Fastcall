import asyncio
import json
from urllib.parse import urlencode, urlsplit, urlunsplit

import websockets
from fastapi import APIRouter, Depends, Request, WebSocket

from fastcall.api.console.auth import console_session, console_write_session, decode_session_token
from fastcall.api.dependencies import get_secret_codec, get_uow
from fastcall.api.requests import MockCallRequest
from fastcall.application.mock_calls import PrepareMockCallUseCase
from fastcall.application.task_control import CancelTaskUseCase
from fastcall_core.domain.errors import RobotConfigError


router = APIRouter(prefix="/console/api/v1")


def _use_case(request, uow):
    settings = request.app.state.settings
    return PrepareMockCallUseCase(
        uow, get_secret_codec(request), request.app.state.phone_hmac,
        callback_url=settings.mock_callback_url,
        callback_secret_ref=settings.mock_callback_secret_ref,
    )


def _adapter_ws_url(request, task_id):
    parsed = urlsplit(request.app.state.settings.adapter_internal_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunsplit((
        scheme, parsed.netloc, f"/internal/v1/mock-calls/{task_id}/browser", "", "",
    ))


@router.post("/mock-calls")
def create_mock_call(
    payload: MockCallRequest,
    request: Request,
    uow=Depends(get_uow),
    session=Depends(console_write_session),
):
    task = _use_case(request, uow).prepare(
        payload.app_id, payload.robot_version_id, payload.variables,
        f"mock:{payload.client_request_id}", session.actor,
    )
    if task["status"] != "draft":
        raise RobotConfigError(
            "MOCK_CALL_NOT_ARMABLE", "An active Mock Call cannot issue another browser ticket"
        )
    ticket = request.app.state.mock_call_tickets.create(task["task_id"], session.actor)
    return {
        "task_id": task["task_id"],
        "ticket": ticket.ticket,
        "ws_url": f"/console/api/v1/mock-calls/{task['task_id']}/ws?{urlencode({'ticket': ticket.ticket})}",
        "expires_at": int(ticket.expires_at),
        "robot_version_id": task["robot_version_id"],
    }


@router.get("/mock-calls/{task_id}")
def get_mock_call(task_id: str, request: Request, uow=Depends(get_uow), session=Depends(console_session)):
    return _use_case(request, uow).get(task_id)


async def _browser_to_adapter(browser: WebSocket, adapter):
    while True:
        message = await browser.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            await adapter.send(message["bytes"])
        elif message.get("text"):
            try:
                control = json.loads(message["text"])
            except json.JSONDecodeError:
                continue
            if control.get("action") in {"answer", "reject", "hangup"}:
                await adapter.send(json.dumps(control))


async def _adapter_to_browser(browser: WebSocket, adapter):
    async for message in adapter:
        if isinstance(message, bytes):
            await browser.send_bytes(message)
        else:
            await browser.send_text(message)


async def _cancel_active_mock(request, task_id, actor):
    uow = request.app.state.uow_factory()
    try:
        task = uow.tasks.get(task_id)
        if (
            task is None or task.origin != "mock_call" or task.created_by != actor
            or task.status in {"completed", "cancelled"}
        ):
            return
        calls = uow.queries.list_calls(task_id, limit=1)
        if calls and calls[0].mock_browser_uuid:
            return
        await CancelTaskUseCase(uow, request.app.state.hangup_client).execute(
            task_id, task.state_version, hangup_active=True
        )
    except Exception:
        pass
    finally:
        uow.close()


@router.websocket("/mock-calls/{task_id}/ws")
async def mock_call_websocket(websocket: WebSocket, task_id: str, ticket: str = ""):
    await websocket.accept()
    request = websocket.app
    adapter = None
    tasks = []
    actor = ""
    claimed = False
    try:
        console = decode_session_token(
            websocket.cookies.get("fastcall_console_session", ""),
            request.state.settings.console_session_secret,
        )
        actor = console.actor
        request.state.mock_call_tickets.claim(task_id, ticket, actor)
        claimed = True
        adapter = await websockets.connect(
            _adapter_ws_url(websocket, task_id),
            additional_headers={"Authorization": f"Bearer {request.state.settings.adapter_token}"},
            open_timeout=10, max_size=None, proxy=None,
        )
        uow = request.state.uow_factory()
        try:
            _use_case(websocket, uow).arm(task_id, actor)
        finally:
            uow.close()
        tasks = [
            asyncio.create_task(_browser_to_adapter(websocket, adapter)),
            asyncio.create_task(_adapter_to_browser(websocket, adapter)),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except RobotConfigError as exc:
        await websocket.send_text(json.dumps({
            "type": "mock.event", "event": "error", "code": exc.code, "message": exc.message,
        }))
    except Exception:
        await websocket.send_text(json.dumps({
            "type": "mock.event", "event": "error",
            "code": "MOCK_CALL_CONNECTION_FAILED", "message": "Mock Call connection ended",
        }))
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if adapter is not None:
            await adapter.close()
        if claimed:
            await _cancel_active_mock(request, task_id, actor)
            request.state.mock_call_tickets.remove(task_id)
        try:
            await websocket.close()
        except Exception:
            pass
