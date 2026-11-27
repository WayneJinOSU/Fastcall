import asyncio
import gzip
import os
import unittest
from unittest.mock import AsyncMock, patch

from pipecat.frames.frames import (
    EndFrame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
)
from websockets.protocol import State
from pipecat.clocks.system_clock import SystemClock
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

from collection_agents.services.providers import create_stt_service, create_tts_service
from collection_agents.services.volcengine_protocol import (
    AUDIO_ONLY_REQUEST,
    COMPRESSION_GZIP,
    COMPRESSION_NONE,
    ERROR_INFORMATION,
    FULL_SERVER_RESPONSE,
    SERIALIZATION_JSON,
    json_payload,
    make_event_packet,
    make_packet,
    parse_packet,
)
from collection_agents.services.volcengine_stt import VolcengineSTTService
from collection_agents.services.volcengine_tts import VolcengineTTSService


class FakeWebSocket:
    def __init__(self):
        self.state = State.OPEN
        self.sent = []
        self.messages = []
        self.received = []
        self.queue = asyncio.Queue()
        self.connect_calls = 0

    async def send(self, data):
        self.sent.append(data)
        response = parse_packet(data)
        event = response["event"]
        session_id = response["session_id"]
        if event == 1:
            self.messages.append(make_event_packet(50, {}, "connection")
            )
        elif event == 100:
            self.messages.append(make_event_packet(150, {}, session_id))
        elif event == 200:
            self.messages.append(
                make_packet(
                    0b1011, 0b0100, 0, COMPRESSION_NONE, b"\x01\x02",
                    event=352, session_id=session_id,
                )
            )
            self.messages.append(make_event_packet(152, {}, session_id))
        self.received.extend(self.messages)
        for message in self.messages:
            self.queue.put_nowait(message)
        self.messages.clear()

    async def close(self):
        self.state = State.CLOSED

    def __aiter__(self):
        async def messages():
            while True:
                yield await self.queue.get()
        return messages()


class VolcengineProtocolTests(unittest.TestCase):
    def test_json_event_and_gzip_audio_round_trip(self):
        event_packet = make_event_packet(100, {"text": "hello"}, "session-1")
        parsed = parse_packet(event_packet)
        self.assertEqual(parsed["event"], 100)
        self.assertEqual(parsed["session_id"], "session-1")
        self.assertEqual(parsed["payload"]["text"], "hello")

        audio_packet = make_packet(
            AUDIO_ONLY_REQUEST, 0, 0, COMPRESSION_GZIP, gzip.compress(b"\x00\x01")
        )
        self.assertEqual(parse_packet(audio_packet)["payload"], b"\x00\x01")

    def test_error_packet_includes_error_code_and_json_payload(self):
        payload = json_payload({"error": "resource ID is mismatched with speaker"})
        packet = make_packet(
            ERROR_INFORMATION, 0, SERIALIZATION_JSON, COMPRESSION_NONE, payload
        )
        packet = packet[:4] + (55000000).to_bytes(4, "big") + packet[4:]
        parsed = parse_packet(packet)
        self.assertEqual(parsed["error_code"], 55000000)
        self.assertIn("mismatched", parsed["payload"]["error"])

    def test_stt_sends_audio_and_maps_interim_and_final_transcripts(self):
        async def scenario():
            service = VolcengineSTTService(api_key="key", locale="zh-CN")
            websocket = FakeWebSocket()
            service._websocket = websocket
            frames = []
            service.push_frame = AsyncMock(side_effect=frames.append)
            service.start_processing_metrics = AsyncMock()
            service.stop_processing_metrics = AsyncMock()

            async for _ in service.run_stt(b"\x01\x00" * 160):
                pass
            audio = parse_packet(websocket.sent[0])
            self.assertEqual(audio["message_type"], AUDIO_ONLY_REQUEST)
            self.assertGreater(len(audio["payload"]), 600)
            self.assertEqual(len(audio["payload"]) % 2, 0)

            for text, definite in (("你", False), ("你好", True)):
                result = {
                    "result": {
                        "text": text,
                        "utterances": [{"definite": definite, "text": text}],
                    },
                }
                websocket.messages.append(make_packet(
                    FULL_SERVER_RESPONSE,
                    0b0001,
                    SERIALIZATION_JSON,
                    COMPRESSION_GZIP,
                    gzip.compress(json_payload(result)),
                    sequence=2,
                ))
            for message in websocket.messages:
                websocket.queue.put_nowait(message)
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(service._receive_messages(), timeout=0.1)
            self.assertIsInstance(frames[0], InterimTranscriptionFrame)
            self.assertIsInstance(frames[1], TranscriptionFrame)
            self.assertEqual(frames[1].text, "你好")

        asyncio.run(scenario())

    def test_stt_enables_server_side_endpointing(self):
        async def scenario():
            websocket = FakeWebSocket()
            service = VolcengineSTTService(
                api_key="key", locale="zh-CN", connect_fn=AsyncMock(return_value=websocket)
            )
            task_manager = TaskManager()
            task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
            clock = SystemClock()
            clock.start()
            await service.setup(FrameProcessorSetup(clock=clock, task_manager=task_manager))
            await service.start(StartFrame())
            await asyncio.sleep(0)
            request = parse_packet(websocket.sent[0])["payload"]["request"]
            self.assertEqual(request["end_window_size"], 600)
            await service.stop(EndFrame())

        asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    def test_tts_sends_task_request_only_within_active_session(self):
        class StreamingWebSocket(FakeWebSocket):
            async def send(self, data):
                response = parse_packet(data)
                event = response["event"]
                session_id = response["session_id"]
                self.sent.append(data)
                if event == 1:
                    self.queue.put_nowait(make_event_packet(50, {}, "connection"))
                elif event == 100:
                    self.queue.put_nowait(make_event_packet(150, {}, session_id))
                elif event == 200:
                    self.queue.put_nowait(make_packet(
                        0b1011, 0b0100, 0, COMPRESSION_NONE, b"\x01\x02",
                        event=352, session_id=session_id,
                    ))
                    task_requests = sum(
                        1 for sent in self.sent if parse_packet(sent)["event"] == 200
                    )
                    if task_requests == 2:
                        self.queue.put_nowait(make_event_packet(152, {}, session_id))

        async def scenario():
            websocket = StreamingWebSocket()

            async def connect(*args, **kwargs):
                websocket.connect_calls += 1
                return websocket

            service = VolcengineTTSService(
                api_key="key", speaker="voice", connect_fn=connect
            )
            task_manager = TaskManager()
            task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
            clock = SystemClock()
            clock.start()
            await service.setup(FrameProcessorSetup(clock=clock, task_manager=task_manager))
            await service.start(StartFrame())
            for _ in range(2):
                async for _ in service.run_tts("text", "session-shared"):
                    pass
            await service.stop(EndFrame())
            events = [parse_packet(data)["event"] for data in websocket.sent]

            self.assertEqual(events.count(100), 1)
            self.assertEqual(events.count(200), 2)

        asyncio.run(asyncio.wait_for(scenario(), timeout=10))

    def test_tts_reuses_connection_for_sessions_and_cancels_on_interruption(self):
        async def scenario():
            websocket = FakeWebSocket()

            async def connect(*args, **kwargs):
                websocket.connect_calls += 1
                return websocket

            service = VolcengineTTSService(
                api_key="key", speaker="voice", connect_fn=connect
            )
            task_manager = TaskManager()
            task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
            clock = SystemClock()
            clock.start()
            await service.setup(FrameProcessorSetup(clock=clock, task_manager=task_manager))
            await service.start(StartFrame())
            for text in ("first", "second"):
                async for _ in service.run_tts(text, f"session-{text}"):
                    pass
            await service.on_audio_context_interrupted("session-second")
            await service.stop(EndFrame())
            events = [
                parse_packet(data)["event"]
                for data in websocket.sent + websocket.received
            ]

            self.assertEqual(websocket.connect_calls, 1)
            self.assertEqual(events.count(100), 2)
            self.assertIn(101, events)
            self.assertIn(352, events)
            self.assertIn(152, events)

        asyncio.run(asyncio.wait_for(scenario(), timeout=10))


class VolcengineFactoryTests(unittest.TestCase):
    def test_stt_requires_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "VOLCENGINE_API_KEY"):
                create_stt_service({"provider": "volcengine"})

    def test_tts_requires_api_key_and_voice(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "VOLCENGINE_API_KEY"):
                create_tts_service({"provider": "volcengine", "voice_id": "voice"})
        with patch.dict(os.environ, {"VOLCENGINE_API_KEY": "key"}, clear=True):
            with self.assertRaisesRegex(ValueError, "voice_id"):
                create_tts_service({"provider": "volcengine"})


if __name__ == "__main__":
    unittest.main()
