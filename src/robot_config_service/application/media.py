import asyncio
import datetime
import json
import logging
from dataclasses import dataclass

import websockets

from robot_config_service.application.call_events import AgentSessionUseCase
from robot_config_service.application.call_results import FinalizeCallUseCase
from robot_config_service.application.dto import utcnow
from robot_config_service.infrastructure.freeswitch.media import (
    AgentReadyTimeout,
    MediaBridge,
    MediaBackpressureOverflow,
)


logger = logging.getLogger(__name__)


@dataclass
class _PendingMedia:
    generation: int
    agent_ws: object
    bridge: object = None
    ready_received: bool = False


class AdapterMediaRuntime:
    def __init__(self, uow_factory, secret_codec, gateway, agent_ws_url, agent_token, clock=utcnow):
        self.uow_factory = uow_factory
        self.secret_codec = secret_codec
        self.gateway = gateway
        self.agent_ws_url = agent_ws_url
        self.agent_token = agent_token
        self.clock = clock
        self._pending = {}
        self._ready_futures = {}
        self._recovery_attempts = {}
        self._recovery_tasks = set()
        self._starting = set()
        self._lock = asyncio.Lock()

    async def attach_freeswitch(self, call_id, websocket):
        async with self._lock:
            pending = self._pending.get(call_id)
            if pending is None:
                raise RuntimeError("No Agent media session is waiting for this Call")
            future = self._ready_futures.get(call_id)
            if future is None or future.done():
                raise RuntimeError("Media stream has already been attached")
            bridge = MediaBridge(
                websocket,
                pending.agent_ws,
                call_id,
                on_hangup=lambda code: self._hangup(call_id, code),
                on_agent_event=lambda event: self._agent_event(call_id, event),
            )
            pending.bridge = bridge
            future.set_result(bridge)
            if pending.ready_received:
                await bridge.mark_ready()
        await bridge.finished.wait()

    async def _agent_event(self, call_id, event):
        action = event.get("event", "").removeprefix("session.")
        if action not in ("attached", "ready", "failed", "completed"):
            return
        uow = self.uow_factory()
        try:
            occurred = datetime.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
            occurred = occurred.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            AgentSessionUseCase(uow, self.secret_codec, self.clock).handle(
                call_id, action, event["claim_generation"], event["agent_version"],
                occurred, event["event_id"], event.get("detail"),
            )
            pending = self._pending.get(call_id)
            await pending.agent_ws.send(json.dumps({
                "event": "ack", "event_id": event["event_id"]
            }))
            if action == "ready":
                pending.ready_received = True
                self._recovery_attempts.pop(call_id, None)
                if pending.bridge is not None:
                    await pending.bridge.mark_ready()
            if action in ("failed", "completed"):
                await self._hangup(call_id, 480)
        finally:
            uow.close()

    async def _hangup(self, call_id, code):
        uow = self.uow_factory()
        try:
            call = uow.calls.get(call_id)
            if call and call.freeswitch_uuid:
                await self.gateway.kill_channel(call.freeswitch_uuid, str(code))
        finally:
            uow.close()

    async def run_call(self, call_id, recover=False):
        async with self._lock:
            if call_id in self._starting or call_id in self._pending:
                return
            self._starting.add(call_id)
        try:
            claim_uow = self.uow_factory()
            try:
                claim = AgentSessionUseCase(claim_uow, self.secret_codec, self.clock).claim(
                    call_id, recover=recover
                )
            finally:
                claim_uow.close()
            generation = claim["claim_generation"]
            agent_ws = await websockets.connect(
                self.agent_ws_url,
                additional_headers={"Authorization": f"Bearer {self.agent_token}"},
            )
        except Exception:
            async with self._lock:
                self._starting.discard(call_id)
            raise
        pending = _PendingMedia(generation, agent_ws)
        future = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending[call_id] = pending
            self._ready_futures[call_id] = future
        try:
            await agent_ws.send(json.dumps(claim["start_payload"]))
            attached = json.loads(await asyncio.wait_for(agent_ws.recv(), timeout=5))
            await self._agent_event(call_id, attached)
            uow = self.uow_factory()
            try:
                call = uow.calls.get(call_id)
                await self.gateway.start_media(call.freeswitch_uuid, call_id, generation)
            finally:
                uow.close()
            bridge = await asyncio.wait_for(future, timeout=10)
            await bridge.run()
            logger.info("Media bridge finished call_id=%s", call_id)
            await asyncio.sleep(0.2)
            uow = self.uow_factory()
            try:
                call = uow.calls.get(call_id)
                active = call and call.status not in ("succeeded", "failed", "cancelled", "unknown")
            finally:
                uow.close()
            if active:
                raise RuntimeError("Media bridge closed before the Call reached a terminal state")
        except websockets.exceptions.ConnectionClosed:
            uow = self.uow_factory()
            try:
                call = uow.calls.get(call_id)
                if call and call.session_ready_at is None:
                    return
            finally:
                uow.close()
            await self._fail_call(call_id, generation, "AI_PROVIDER_FAILED", "Agent disconnected")
        except Exception as error:
            result_code = (
                "MEDIA_BACKPRESSURE_OVERFLOW"
                if isinstance(error, MediaBackpressureOverflow)
                else "AGENT_READY_TIMEOUT" if isinstance(error, AgentReadyTimeout)
                else "MEDIA_CONNECTION_TIMEOUT" if isinstance(error, asyncio.TimeoutError)
                else "AI_PROVIDER_FAILED"
            )
            await self._fail_call(call_id, generation, result_code, str(error))
        finally:
            stop_uow = self.uow_factory()
            try:
                call = stop_uow.calls.get(call_id)
                if call and call.freeswitch_uuid:
                    await self.gateway.stop_media(call.freeswitch_uuid)
            except Exception as error:
                logger.warning("Media stop failed call_id=%s error=%s", call_id, type(error).__name__)
            finally:
                stop_uow.close()
            await agent_ws.close()
            async with self._lock:
                self._pending.pop(call_id, None)
                self._ready_futures.pop(call_id, None)
                self._starting.discard(call_id)

    async def _fail_call(self, call_id, generation, result_code, message):
        uow = self.uow_factory()
        try:
            call = uow.calls.get(call_id)
            if call and call.status not in ("succeeded", "failed", "cancelled", "unknown"):
                FinalizeCallUseCase(uow, self.clock).execute(
                    call_id, "failed", result_code, "failed",
                    generation=generation, failure_code=result_code,
                    failure_message=message, termination_reason="ADAPTER_MEDIA_FAILED",
                    session_failed=True,
                )
            if call and call.freeswitch_uuid:
                await self.gateway.kill_channel(call.freeswitch_uuid, "NORMAL_CLEARING")
        finally:
            uow.close()

    async def recover_sessions(self, max_attempts=3):
        uow = self.uow_factory()
        try:
            calls = uow.dispatch.list_recoverable_sessions()
        finally:
            uow.close()
        for call in calls:
            if call.id in self._pending or call.id in self._starting:
                continue
            attempts = self._recovery_attempts.get(call.id, 0)
            if attempts >= max_attempts:
                await self._fail_call(
                    call.id, call.claim_generation, "AI_PROVIDER_FAILED",
                    "Agent pre-ready recovery attempts exhausted",
                )
                self._recovery_attempts.pop(call.id, None)
                continue
            self._recovery_attempts[call.id] = attempts + 1
            task = asyncio.create_task(self.run_call(call.id, recover=True))
            self._recovery_tasks.add(task)
            task.add_done_callback(self._recovery_tasks.discard)
