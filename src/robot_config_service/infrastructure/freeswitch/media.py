import asyncio
import json
import logging


logger = logging.getLogger(__name__)


PCM_FRAME_BYTES = 320
PCM_FRAME_INTERVAL_SEC = 0.02
MAX_AUDIO_FRAMES = 25


class MediaBackpressureOverflow(RuntimeError):
    pass


class AgentReadyTimeout(RuntimeError):
    pass


class PCMFrameReassembler:
    def __init__(self, frame_bytes=PCM_FRAME_BYTES):
        self.frame_bytes = frame_bytes
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= self.frame_bytes:
            frames.append(bytes(self.buffer[:self.frame_bytes]))
            del self.buffer[:self.frame_bytes]
        return frames


def _drop_oldest_put(queue, frame):
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(frame)


class MediaBridge:
    def __init__(
        self, freeswitch_ws, agent_ws, call_id, on_hangup=None, on_agent_event=None,
        ready_timeout_sec=30,
    ):
        self.freeswitch_ws = freeswitch_ws
        self.ready_timeout_sec = ready_timeout_sec
        self.agent_ws = agent_ws
        self.call_id = call_id
        self.on_hangup = on_hangup
        self.on_agent_event = on_agent_event
        self.caller_frames = asyncio.Queue(maxsize=MAX_AUDIO_FRAMES)
        self.agent_frames = asyncio.Queue(maxsize=MAX_AUDIO_FRAMES)
        self.pre_ready_frames = []
        self.ready = asyncio.Event()
        self.dropped_caller_frames = 0
        self._agent_reassembler = PCMFrameReassembler()
        self._tasks = []
        self.finished = asyncio.Event()
        self.hangup_requested = False
        self._agent_completed = asyncio.Event()

    async def mark_ready(self):
        if self.ready.is_set():
            return
        await self.agent_ws.send(json.dumps({
            "action": "answer", "data": {"callUUID": self.call_id}
        }))
        self.ready.set()
        for frame in self.pre_ready_frames:
            if self.caller_frames.full():
                self.caller_frames.get_nowait()
                self.dropped_caller_frames += 1
            self.caller_frames.put_nowait(frame)
        self.pre_ready_frames.clear()

    async def accept_caller_bytes(self, data, reassembler):
        for frame in reassembler.feed(data):
            if not self.ready.is_set():
                if len(self.pre_ready_frames) == MAX_AUDIO_FRAMES:
                    self.pre_ready_frames.pop(0)
                    self.dropped_caller_frames += 1
                self.pre_ready_frames.append(frame)
            else:
                if self.caller_frames.full():
                    self.caller_frames.get_nowait()
                    self.dropped_caller_frames += 1
                self.caller_frames.put_nowait(frame)

    async def accept_agent_bytes(self, data):
        for frame in self._agent_reassembler.feed(data):
            if self.agent_frames.full():
                raise MediaBackpressureOverflow("Agent audio output exceeded 500ms")
            self.agent_frames.put_nowait(frame)

    async def _freeswitch_receive(self):
        reassembler = PCMFrameReassembler()
        while True:
            message = await self.freeswitch_ws.receive()
            if message.get("type") == "websocket.disconnect":
                logger.info(
                    "FreeSWITCH media disconnected call_id=%s code=%s reason=%s",
                    self.call_id, message.get("code"), message.get("reason"),
                )
                break
            if message.get("bytes"):
                await self.accept_caller_bytes(message["bytes"], reassembler)

    async def _agent_receive(self):
        while True:
            data = await self.agent_ws.recv()
            if isinstance(data, bytes):
                if not self.hangup_requested:
                    await self.accept_agent_bytes(data)
                continue
            event = json.loads(data)
            if event.get("action") == "hangup" and self.on_hangup:
                logger.info("Agent requested hangup call_id=%s", self.call_id)
                self.hangup_requested = True
                await self.on_hangup(event.get("data", {}).get("code", 480))
                continue
            if self.on_agent_event is not None:
                await self.on_agent_event(event)
            if event.get("event") == "session.completed":
                self._agent_completed.set()
                break

    async def _agent_send(self):
        while True:
            frame = await self.caller_frames.get()
            await self.agent_ws.send(frame)

    async def _freeswitch_send(self):
        await self.freeswitch_ws.send_text(json.dumps({
            "type": "rawAudio", "data": {"sampleRate": 8000}
        }, separators=(",", ":")))
        loop = asyncio.get_running_loop()
        next_send = loop.time()
        while True:
            frame = await self.agent_frames.get()
            next_send = max(next_send, loop.time())
            await asyncio.sleep(max(0, next_send - loop.time()))
            try:
                await self.freeswitch_ws.send_bytes(frame)
            except Exception:
                if not self.hangup_requested:
                    raise
                break
            next_send += PCM_FRAME_INTERVAL_SEC

    async def _ready_watchdog(self):
        try:
            await asyncio.wait_for(self.ready.wait(), timeout=self.ready_timeout_sec)
        except asyncio.TimeoutError as error:
            raise AgentReadyTimeout("Agent session was not ready before the deadline") from error
        await self.finished.wait()

    async def run(self):
        self._tasks = [
            asyncio.create_task(self._ready_watchdog(), name="ready-watchdog"),
            asyncio.create_task(self._freeswitch_receive(), name="freeswitch-receive"),
            asyncio.create_task(self._agent_receive(), name="agent-receive"),
            asyncio.create_task(self._agent_send(), name="agent-send"),
            asyncio.create_task(self._freeswitch_send(), name="freeswitch-send"),
        ]
        try:
            done, pending = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
            logger.info(
                "Media tasks completed call_id=%s tasks=%s",
                self.call_id,
                [
                    (task.get_name(), task.cancelled(), repr(task.exception()) if not task.cancelled() else None)
                    for task in done
                ],
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                exception = task.exception()
                if exception:
                    raise exception
        finally:
            self.finished.set()

    async def close(self):
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for websocket in (self.agent_ws, self.freeswitch_ws):
            close = getattr(websocket, "close", None)
            if close:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
