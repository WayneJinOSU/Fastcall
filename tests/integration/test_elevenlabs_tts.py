import asyncio
import base64
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import EndFrame, StartFrame, TTSAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.elevenlabs import tts as elevenlabs_tts_module
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams
from websockets.asyncio.client import connect as websocket_connect
from websockets.asyncio.server import serve

from collection_agents.services.providers import create_tts_service


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

RUN_INTEGRATION = os.getenv("RUN_ELEVENLABS_INTEGRATION", "").strip() == "1"
INTEGRATION_READY = bool(
    RUN_INTEGRATION
    and os.getenv("ELEVENLABS_API_KEY", "").strip()
    and os.getenv("ELEVENLABS_VOICE_ID", "").strip()
)


class ElevenLabsTTSIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_adapter_streams_8khz_pcm_audio(self):
        async def handler(websocket):
            async for raw_message in websocket:
                message = json.loads(raw_message)
                if message.get("close_socket"):
                    break
                if not message.get("text", "").strip():
                    continue
                context_id = message["context_id"]
                audio = base64.b64encode(b"\x01\x00" * 320).decode("ascii")
                await websocket.send(
                    json.dumps({"contextId": context_id, "audio": audio})
                )
                await websocket.send(
                    json.dumps({"contextId": context_id, "isFinal": True})
                )

        def connect_without_proxy(*args, **kwargs):
            return websocket_connect(*args, proxy=None, **kwargs)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with (
                patch.dict(
                    os.environ,
                    {
                        "ELEVENLABS_API_KEY": "test-key",
                        "ELEVENLABS_VOICE_ID": "test-voice",
                    },
                    clear=True,
                ),
                patch.object(
                    elevenlabs_tts_module,
                    "websocket_connect",
                    connect_without_proxy,
                ),
            ):
                service = create_tts_service(
                    {"provider": "elevenlabs", "locale": "en-US", "speed": 1.0}
                )
                service._url = f"ws://127.0.0.1:{port}"
                audio_frames = await self._collect_audio(
                    service, "Local adapter test", 10
                )

        self.assert_audio_frames(audio_frames)

    @unittest.skipUnless(
        INTEGRATION_READY,
        "Set RUN_ELEVENLABS_INTEGRATION=1, ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID to run this test.",
    )
    async def test_elevenlabs_api_streams_8khz_pcm_audio(self):
        service = create_tts_service(
            {
                "provider": "elevenlabs",
                "model": os.getenv("ELEVENLABS_TEST_MODEL", "eleven_flash_v2_5"),
                "locale": os.getenv("ELEVENLABS_TEST_LOCALE", "en-US"),
                "speed": 1.0,
            }
        )
        audio_frames = await self._collect_audio(
            service,
            os.getenv(
                "ELEVENLABS_TEST_TEXT",
                "Hello, this is a telephony speech synthesis test.",
            ),
            45,
        )
        self.assert_audio_frames(audio_frames)

    async def _collect_audio(self, service, text, timeout):
        task_manager = TaskManager()
        task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
        clock = SystemClock()
        clock.start()
        await service.setup(
            FrameProcessorSetup(clock=clock, task_manager=task_manager)
        )

        audio_frames = []
        audio_received = asyncio.Event()
        original_append = service.append_to_audio_context

        async def record_audio(context_id, frame):
            audio_frames.append(frame)
            audio_received.set()
            await original_append(context_id, frame)

        service.append_to_audio_context = record_audio

        try:
            await service.process_frame(
                StartFrame(enable_metrics=True),
                FrameDirection.DOWNSTREAM,
            )
            async for _ in service.run_tts(text, str(uuid.uuid4())):
                pass
            await asyncio.wait_for(audio_received.wait(), timeout=timeout)
            return audio_frames
        finally:
            await service.stop(EndFrame())
            await service.cleanup()

    def assert_audio_frames(self, audio_frames):
        self.assertTrue(audio_frames)
        for frame in audio_frames:
            self.assertIsInstance(frame, TTSAudioRawFrame)
            self.assertGreater(len(frame.audio), 0)
            self.assertEqual(frame.sample_rate, 8000)
            self.assertEqual(frame.num_channels, 1)


if __name__ == "__main__":
    unittest.main()
