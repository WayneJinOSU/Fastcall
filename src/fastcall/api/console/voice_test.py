import asyncio
import json
import logging

import websockets
from fastapi import APIRouter, Depends, Request, WebSocket

from fastcall.api.console.auth import (
    console_write_session,
    decode_session_token,
)
from fastcall.api.dependencies import get_uow
from fastcall.api.requests import VoiceTestSessionRequest
from fastcall.application.validation import ValidateConfigUseCase
from fastcall.application.voice_test import VoiceTestUseCase
from fastcall_core.domain.errors import RobotConfigError


router = APIRouter(prefix="/console/api/v1")


@router.post("/robot-apps/{app_id}/voice-test/sessions")
def create_voice_test_session(
    app_id: str,
    payload: VoiceTestSessionRequest,
    request: Request,
    uow=Depends(get_uow),
    session=Depends(console_write_session),
):
    return VoiceTestUseCase(
        uow,
        ValidateConfigUseCase(request.app.state.catalog),
        request.app.state.voice_test_sessions,
    ).create(app_id, payload.draft_revision, payload.variables, session.actor)


async def _send_error(client: WebSocket, code: str, message: str):
    try:
        await client.send_text(json.dumps({
            "type": "test.event",
            "event": "error",
            "code": code,
            "message": message,
        }))
    except Exception:
        pass


async def _client_to_agent(client: WebSocket, agent):
    while True:
        message = await client.receive()
        if message["type"] == "websocket.disconnect":
            return
        audio = message.get("bytes")
        if audio is not None:
            if len(audio) > 65536:
                raise RobotConfigError("VOICE_TEST_AUDIO_TOO_LARGE", "Audio frame is too large")
            await agent.send(audio)
            continue
        text = message.get("text")
        if not text:
            continue
        try:
            control = json.loads(text)
        except json.JSONDecodeError:
            continue
        if control.get("action") == "hangup":
            await agent.send(json.dumps({"action": "hangup", "data": {}}))


async def _agent_to_client(client: WebSocket, agent):
    async for message in agent:
        if isinstance(message, bytes):
            await client.send_bytes(message)
        else:
            await client.send_text(message)


@router.websocket("/voice-test/{session_id}/ws")
async def voice_test_websocket(websocket: WebSocket, session_id: str, ticket: str = ""):
    await websocket.accept()
    settings = websocket.app.state.settings
    store = websocket.app.state.voice_test_sessions
    session = None
    agent = None
    tasks = []
    try:
        console = decode_session_token(
            websocket.cookies.get("fastcall_console_session", ""),
            settings.console_session_secret,
        )
        session = store.claim(session_id, ticket, console.actor)
        try:
            agent = await websockets.connect(
                settings.collection_agent_ws_url,
                open_timeout=10,
                max_size=None,
                proxy=None,
            )
        except asyncio.TimeoutError as exc:
            raise RobotConfigError(
                "VOICE_TEST_AGENT_UNAVAILABLE",
                "Unable to connect to collection-agent",
            ) from exc
        await agent.send(json.dumps(session.start_payload(), ensure_ascii=False))
        await agent.send(json.dumps({
            "action": "answer",
            "data": {"callUUID": session.call_uuid},
        }))
        await websocket.send_text(json.dumps({
            "type": "test.event",
            "event": "session.ready",
            "session_id": session.session_id,
        }))
        tasks = [
            asyncio.create_task(_client_to_agent(websocket, agent)),
            asyncio.create_task(_agent_to_client(websocket, agent)),
        ]
        done, _ = await asyncio.wait_for(
            asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED),
            timeout=300,
        )
        for task in done:
            task.result()
    except RobotConfigError as exc:
        await _send_error(websocket, exc.code, exc.message)
    except asyncio.TimeoutError:
        await _send_error(websocket, "VOICE_TEST_TIMEOUT", "Voice test reached its time limit")
    except Exception as exc:
        logging.info("Voice test websocket ended: %s", exc)
        await _send_error(websocket, "VOICE_TEST_CONNECTION_FAILED", "Voice test connection ended")
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if agent is not None:
            await agent.close()
        if session is not None:
            store.remove(session.session_id)
        try:
            await websocket.close()
        except Exception:
            pass
