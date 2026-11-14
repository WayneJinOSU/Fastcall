import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock

from collection_agents.session_lifecycle import SessionFastAPIWebsocketTransport, SessionLifecycle
from fastcall_adapter.application.media import AdapterMediaRuntime, _PendingMedia
from fastcall_adapter.application.media_bridge import (
    MAX_AUDIO_FRAMES,
    PCM_FRAME_BYTES,
    AgentReadyTimeout,
    MediaBackpressureOverflow,
    MediaBridge,
    PCMFrameReassembler,
)


class FakeAgentSocket:
    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()

    async def send(self, value):
        self.sent.append(value)

    async def recv(self):
        return await self.incoming.get()


class FakeFreeSwitchSocket:
    def __init__(self):
        self.text = []
        self.binary = []

    async def send_text(self, value):
        self.text.append(value)

    async def send_bytes(self, value):
        self.binary.append(value)


class MediaTests(unittest.TestCase):
    def test_pipeline_disconnect_keeps_fastcall_websocket_open(self):
        async def scenario():
            websocket = Mock()
            websocket.close = AsyncMock()
            transport = SessionFastAPIWebsocketTransport(websocket, Mock())
            await transport._client.disconnect()
            self.assertTrue(transport._client.is_closing)
            websocket.close.assert_not_awaited()

        asyncio.run(scenario())

    def test_session_ack_ignores_binary_audio(self):
        async def scenario():
            websocket = Mock()
            websocket.send_json = AsyncMock()
            websocket.receive = AsyncMock(side_effect=[
                {"type": "websocket.receive", "bytes": b"\x00" * 320},
                {"type": "websocket.receive", "text": json.dumps({
                    "event": "ack", "event_id": "agent:call_1:1:completed"
                })},
            ])
            lifecycle = SessionLifecycle(websocket, "call_1", 1, "sha256:test")
            await lifecycle.completed()
            self.assertEqual(lifecycle.state, "completed")

        asyncio.run(scenario())

    def test_reassembler_does_not_depend_on_message_boundaries(self):
        reassembler = PCMFrameReassembler()
        frame = bytes(range(256)) + bytes(range(64))
        self.assertEqual(reassembler.feed(frame[:1]), [])
        self.assertEqual(reassembler.feed(frame[1:319]), [])
        self.assertEqual(reassembler.feed(frame[319:] + b"x"), [frame])
        self.assertEqual(reassembler.buffer, b"x")

    def test_pre_ready_audio_is_bounded_and_flushed_after_answer(self):
        async def scenario():
            agent = FakeAgentSocket()
            bridge = MediaBridge(FakeFreeSwitchSocket(), agent, "call_1")
            reassembler = PCMFrameReassembler()
            for index in range(MAX_AUDIO_FRAMES + 1):
                await bridge.accept_caller_bytes(bytes([index]) * 320, reassembler)
            self.assertEqual(len(bridge.pre_ready_frames), MAX_AUDIO_FRAMES)
            self.assertEqual(bridge.dropped_caller_frames, 1)
            self.assertEqual(agent.sent, [])
            await bridge.mark_ready()
            answer = json.loads(agent.sent[0])
            self.assertEqual(answer["action"], "answer")
            self.assertEqual(bridge.caller_frames.qsize(), MAX_AUDIO_FRAMES)
            self.assertEqual(bridge.caller_frames.get_nowait()[0], 1)

        asyncio.run(scenario())

    def test_agent_output_overflow_fails_instead_of_growing_memory(self):
        async def scenario():
            bridge = MediaBridge(FakeFreeSwitchSocket(), FakeAgentSocket(), "call_1")
            await bridge.accept_agent_bytes(b"a" * 160)
            await bridge.accept_agent_bytes(b"b" * 160)
            self.assertEqual(bridge.agent_frames.qsize(), 1)
            frame = bridge.agent_frames.get_nowait()
            self.assertEqual(frame, b"a" * 160 + b"b" * 160)
            await bridge.accept_agent_bytes(b"x" * (MAX_AUDIO_FRAMES * PCM_FRAME_BYTES))
            with self.assertRaises(MediaBackpressureOverflow):
                await bridge.accept_agent_bytes(b"y" * PCM_FRAME_BYTES)

        asyncio.run(scenario())

    def test_freeswitch_write_failure_is_idempotent_after_hangup(self):
        async def scenario():
            fs = FakeFreeSwitchSocket()
            fs.send_bytes = AsyncMock(side_effect=RuntimeError("disconnected"))
            bridge = MediaBridge(fs, FakeAgentSocket(), "call_1")
            bridge.hangup_requested = True
            await bridge.accept_agent_bytes(b"a" * 320)
            await bridge._freeswitch_send()

        asyncio.run(scenario())

    def test_agent_output_is_sent_as_raw_pcm_at_twenty_ms_pacing(self):
        async def scenario():
            fs = FakeFreeSwitchSocket()
            bridge = MediaBridge(fs, FakeAgentSocket(), "call_1")
            task = asyncio.create_task(bridge._freeswitch_send())
            await bridge.accept_agent_bytes(b"a" * 640)
            await asyncio.sleep(0.06)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            header = json.loads(fs.text[0])
            self.assertEqual(header, {"type": "rawAudio", "data": {"sampleRate": 8000}})
            self.assertEqual(fs.binary, [b"a" * 320, b"a" * 320])

        asyncio.run(scenario())

    def test_ready_before_freeswitch_connection_flushes_bridge_on_attach(self):
        async def scenario():
            agent = FakeAgentSocket()
            runtime = AdapterMediaRuntime(None, None, None, "ws://unused", "token")
            pending = _PendingMedia(1, agent, ready_received=True)
            future = asyncio.get_running_loop().create_future()
            runtime._pending["call_1"] = pending
            runtime._ready_futures["call_1"] = future
            attach = asyncio.create_task(runtime.attach_freeswitch("call_1", FakeFreeSwitchSocket()))
            await asyncio.sleep(0)
            bridge = pending.bridge
            self.assertTrue(bridge.ready.is_set())
            self.assertEqual(json.loads(agent.sent[0])["action"], "answer")
            bridge.finished.set()
            await attach

        asyncio.run(scenario())

    def test_agent_hangup_keeps_receiving_until_session_completed(self):
        async def scenario():
            agent = FakeAgentSocket()
            on_hangup = AsyncMock()
            on_agent_event = AsyncMock()
            bridge = MediaBridge(
                FakeFreeSwitchSocket(), agent, "call_1",
                on_hangup=on_hangup, on_agent_event=on_agent_event,
            )
            receive = asyncio.create_task(bridge._agent_receive())
            await agent.incoming.put(json.dumps({"action": "hangup", "data": {"code": 480}}))
            await asyncio.sleep(0.01)
            self.assertFalse(receive.done())
            on_hangup.assert_awaited_once_with(480)
            await agent.incoming.put(b"x" * (MAX_AUDIO_FRAMES * PCM_FRAME_BYTES * 2))
            await agent.incoming.put(json.dumps({"event": "session.completed"}))
            await receive
            self.assertEqual(bridge.agent_frames.qsize(), 0)
            self.assertTrue(bridge.hangup_requested)
            self.assertTrue(bridge._agent_completed.is_set())

        asyncio.run(scenario())

    def test_ready_watchdog_does_not_finish_media_after_ready(self):
        async def scenario():
            bridge = MediaBridge(FakeFreeSwitchSocket(), FakeAgentSocket(), "call_1")
            await bridge.mark_ready()
            watchdog = asyncio.create_task(bridge._ready_watchdog())
            await asyncio.sleep(0.01)
            self.assertFalse(watchdog.done())
            bridge.finished.set()
            await watchdog

        asyncio.run(scenario())

    def test_ready_watchdog_times_out(self):
        async def scenario():
            bridge = MediaBridge(
                FakeFreeSwitchSocket(), FakeAgentSocket(), "call_1", ready_timeout_sec=0.01
            )
            with self.assertRaises(AgentReadyTimeout):
                await bridge._ready_watchdog()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
