import asyncio
import json
from dataclasses import dataclass, field

from fastcall_adapter.application.media_bridge import PCMFrameReassembler
from fastcall_core.application.call_results import FinalizeCallUseCase
from fastcall_core.domain.telephony_results import SUCCESS_RESULTS, map_channel_hangup


MAX_AUDIO_FRAMES = 25
FRAME_INTERVAL_SEC = 0.02


def _debug_dump(call_id, direction, data):
    # try:
    #     with open(f"/tmp/mock_{call_id}_{direction}.pcm", "ab") as handle:
    #         handle.write(data)
    # except Exception:
    #     pass
    pass



@dataclass
class _MockSession:
    task_id: str
    browser: object
    call_id: str | None = None
    browser_uuid: str | None = None
    freeswitch: object = None
    answered: bool = False
    active: bool = False
    media_started: bool = False
    ending: bool = False
    ending_at: float | None = None
    hangup_cause: str | None = None
    terminal: bool = False
    agent_ready: bool = False
    a_media_bound: bool = False
    outgoing: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=MAX_AUDIO_FRAMES))
    incoming: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=MAX_AUDIO_FRAMES))
    ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    incoming_timer: asyncio.Task | None = None
    browser_reassembler: PCMFrameReassembler = field(default_factory=PCMFrameReassembler)


class MockCallMediaRuntime:
    def __init__(self, uow_factory, gateway, incoming_timeout=30):
        self.uow_factory = uow_factory
        self.gateway = gateway
        self.incoming_timeout = incoming_timeout
        self._sessions = {}
        self._by_call = {}
        self._lock = asyncio.Lock()

    def has_browser(self, task_id):
        session = self._sessions.get(task_id)
        return bool(
            session and session.browser is not None
            and not session.terminal and not session.ending
        )

    async def _event(self, session, event, **data):
        if session.browser is None:
            return
        try:
            await session.browser.send_text(json.dumps({
                "type": "mock.event", "event": event, **data,
            }))
        except Exception:
            pass

    async def run_browser(self, task_id, websocket):
        async with self._lock:
            existing = self._sessions.get(task_id)
            if existing and existing.browser is not None and not existing.terminal:
                raise RuntimeError("Mock Call browser is already connected")
            session = _MockSession(task_id, websocket)
            self._sessions[task_id] = session
        await self._event(session, "transport.ready", task_id=task_id)
        try:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message.get("bytes") is not None:
                    await self._accept_browser_audio(session, message["bytes"])
                    continue
                if not message.get("text"):
                    continue
                try:
                    control = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                action = control.get("action")
                if action == "answer":
                    await self.answer(task_id)
                elif action == "reject":
                    await self.terminate(task_id, "CALL_REJECTED")
                elif action == "hangup":
                    await self.terminate(task_id, "NORMAL_CLEARING")
        finally:
            async with self._lock:
                if session.browser == websocket:
                    session.browser = None
            if not session.terminal:
                await self.terminate(task_id, "NORMAL_CLEARING")

    async def _accept_browser_audio(self, session, data):
        if not session.active:
            return
        _debug_dump(session.call_id, "up", data)
        for frame in session.browser_reassembler.feed(data):
            if session.outgoing.full():
                session.outgoing.get_nowait()
            session.outgoing.put_nowait(frame)

    async def handle_freeswitch(self, event):
        task_id = event.get("variable_fastcall_task_id")
        call_id = event.get("variable_fastcall_call_id")
        channel_uuid = event.get("Unique-ID")
        session = self._sessions.get(task_id)
        if session is None or not call_id or not channel_uuid:
            return
        if event["Event-Name"] == "CHANNEL_CREATE":
            uow = self.uow_factory()
            try:
                call = uow.telephony.bind_mock_browser(call_id, channel_uuid)
                uow.commit()
            finally:
                uow.close()
            async with self._lock:
                session.call_id = call_id
                session.browser_uuid = channel_uuid
                self._by_call[call_id] = session
            if session.ending:
                await self._kill_channels(
                    session, call, session.hangup_cause or "NORMAL_CLEARING"
                )
                return
            await self._event(session, "incoming", call_id=call_id)
            session.incoming_timer = asyncio.create_task(self._incoming_timeout(task_id))
        elif event["Event-Name"] == "CHANNEL_ANSWER":
            if session.ending:
                await self.gateway.kill_channel(
                    channel_uuid, session.hangup_cause or "NORMAL_CLEARING"
                )
                return
            if not session.answered:
                session.answered = True
            if not session.media_started:
                await self.gateway.start_mock_media(channel_uuid, call_id)
                session.media_started = True
        elif event["Event-Name"] in {"CHANNEL_DESTROY", "CHANNEL_HANGUP_COMPLETE"}:
            if not session.terminal:
                await self.terminate(task_id, "NORMAL_CLEARING")

    async def _incoming_timeout(self, task_id):
        try:
            await asyncio.sleep(self.incoming_timeout)
            session = self._sessions.get(task_id)
            if session and not session.answered and not session.terminal:
                await self._event(session, "incoming_timeout")
                await self.terminate(task_id, "NO_ANSWER")
        except asyncio.CancelledError:
            pass

    async def answer(self, task_id):
        async with self._lock:
            session = self._sessions.get(task_id)
            if (
                session is None or session.terminal or session.ending
                or session.answered or not session.browser_uuid
            ):
                return
            session.answered = True
            if session.incoming_timer:
                session.incoming_timer.cancel()
        await self._event(session, "answer_accepted", call_id=session.call_id)
        await self.gateway.answer_channel(session.browser_uuid)

    async def terminate(self, task_id, cause="NORMAL_CLEARING"):
        async with self._lock:
            session = self._sessions.get(task_id)
            if session is None or session.terminal or session.ending:
                return
            session.ending = True
            session.ending_at = asyncio.get_running_loop().time()
            session.hangup_cause = cause
            session.ready_event.set()
            if session.incoming_timer:
                session.incoming_timer.cancel()
        await self._event(session, "ending")
        uow = self.uow_factory()
        try:
            call = (
                uow.calls.get(session.call_id) if session.call_id
                else uow.telephony.get_mock_call(task_id)
            )
        finally:
            uow.close()
        if call is not None and session.call_id is None:
            session.call_id = call.id
            self._by_call[call.id] = session
        if call is None or (call.status == "reserved" and call.dial_requested_at is None):
            session.terminal = True
            await self._event(session, "ended", result_code="CANCELLED")
            await self._close_media(session)
            self._sessions.pop(task_id, None)
            if call is not None:
                self._by_call.pop(call.id, None)
            return
        await self._kill_channels(session, call, cause)

    async def _kill_channels(self, session, call, cause):
        if call and (call.freeswitch_uuid or call.origination_uuid):
            try:
                await self.gateway.kill_channel(
                    call.freeswitch_uuid or call.origination_uuid, cause
                )
            except Exception:
                pass
        if session.browser_uuid:
            try:
                await self.gateway.kill_channel(session.browser_uuid, cause)
            except Exception:
                pass
        await self._close_freeswitch(session)

    async def a_media_bound(self, call_id):
        session = self._by_call.get(call_id)
        if session:
            session.a_media_bound = True
            self._update_ready(session)

    async def wait_agent_ready(self, call_id):
        session = self._by_call.get(call_id)
        if session is None or session.ending or session.terminal:
            raise RuntimeError("Mock Call browser session is ending or unavailable")
        session.agent_ready = True
        self._update_ready(session)
        await asyncio.wait_for(session.ready_event.wait(), timeout=30)
        if session.ending or session.terminal:
            raise RuntimeError("Mock Call ended before Agent became ready")

    def _update_ready(self, session):
        if (
            session.agent_ready and session.a_media_bound
            and session.freeswitch is not None and session.browser is not None
            and not session.terminal and not session.ending
        ):
            session.ready_event.set()

    async def mark_active(self, call_id):
        session = self._by_call.get(call_id)
        if session and not session.terminal and not session.ending:
            session.active = True
            await self._event(session, "ready", call_id=call_id)

    async def run_freeswitch(self, call_id, websocket):
        session = self._by_call.get(call_id)
        if session is None or session.browser is None or session.terminal or session.ending:
            raise RuntimeError("No Mock Call browser is waiting for this media")
        session.freeswitch = websocket
        self._update_ready(session)
        tasks = [
            asyncio.create_task(self._freeswitch_receive(session)),
            asyncio.create_task(self._freeswitch_send(session)),
            asyncio.create_task(self._browser_send(session)),
        ]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        finally:
            if not session.terminal:
                await self.terminate(session.task_id, "NORMAL_CLEARING")
            await self._close_freeswitch(session)

    async def _freeswitch_receive(self, session):
        reassembler = PCMFrameReassembler()
        while True:
            message = await session.freeswitch.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes"):
                _debug_dump(session.call_id, "fs_down", message["bytes"])
                for frame in reassembler.feed(message["bytes"]):
                    if session.incoming.full():
                        session.incoming.get_nowait()
                    session.incoming.put_nowait(frame)

    async def _freeswitch_send(self, session):
        await session.freeswitch.send_text(json.dumps({
            "type": "rawAudio", "data": {"sampleRate": 8000},
        }, separators=(",", ":")))
        await self._send_frames(
            session.freeswitch, session.outgoing, debug=("fs_up", session.call_id)
        )

    async def _browser_send(self, session):
        await self._send_frames(
            session.browser, session.incoming, debug=("down", session.call_id)
        )

    async def _send_frames(self, websocket, queue, debug=None):
        loop = asyncio.get_running_loop()
        next_send = loop.time()
        while True:
            frame = await queue.get()
            if debug:
                _debug_dump(debug[1], debug[0], frame)
            next_send = max(next_send, loop.time())
            await asyncio.sleep(max(0, next_send - loop.time()))
            await websocket.send_bytes(frame)
            next_send += FRAME_INTERVAL_SEC

    async def finish(self, call_id, result_code=None):
        async with self._lock:
            session = self._by_call.get(call_id)
            if session is None or session.terminal:
                return
            session.terminal = True
            if session.incoming_timer:
                session.incoming_timer.cancel()
        try:
            await self._event(session, "ended", call_id=call_id, result_code=result_code)
        finally:
            await self._close_media(session)
            self._by_call.pop(call_id, None)
            self._sessions.pop(session.task_id, None)

    async def cleanup_stale(self):
        uow = self.uow_factory()
        try:
            calls = uow.telephony.list_active_mock_calls()
        finally:
            uow.close()
        for call in calls:
            session = self._sessions.get(call.task_id)
            if (
                session and session.browser is not None and not session.terminal
                and not session.ending
            ):
                continue
            if (
                session and session.ending_at is not None
                and asyncio.get_running_loop().time() - session.ending_at < 5
            ):
                continue
            cause = (session.hangup_cause if session else None) or "NORMAL_CLEARING"
            cleaned = True
            channels = {
                call.freeswitch_uuid or call.origination_uuid,
                call.mock_browser_uuid,
            }
            for channel_uuid in channels:
                if not channel_uuid:
                    continue
                try:
                    await self.gateway.kill_channel(channel_uuid, cause)
                except Exception:
                    cleaned = False
            if not cleaned:
                continue
            result_code = map_channel_hangup(
                cause, answered=call.answered_at is not None,
                ready=call.session_ready_at is not None,
            )
            target = "succeeded" if result_code in SUCCESS_RESULTS else "failed"
            finalize_uow = self.uow_factory()
            try:
                current = finalize_uow.calls.get(call.id)
                if current.status not in {"succeeded", "failed", "cancelled", "unknown"}:
                    FinalizeCallUseCase(finalize_uow).execute(
                        call.id, target, result_code, target, allow_retry=False,
                        termination_reason="MOCK_CALL_CLEANUP",
                    )
                result_code = current.result_code or result_code
            finally:
                finalize_uow.close()
            if session:
                self._by_call[call.id] = session
                await self.finish(call.id, result_code)

    async def _close_freeswitch(self, session):
        if session.freeswitch is not None:
            try:
                await session.freeswitch.close()
            except Exception:
                pass

    async def _close_media(self, session):
        await self._close_freeswitch(session)
        if session.browser is not None:
            try:
                await session.browser.close()
            except Exception:
                pass
