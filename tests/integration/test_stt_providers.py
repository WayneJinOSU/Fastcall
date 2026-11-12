import asyncio
import os
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soxr
from dotenv import load_dotenv
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    EndFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

from collection_agents.services.providers import create_stt_service


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
TEST_AUDIO_PATH = PROJECT_ROOT / "assets" / "14287.wav"


def integration_ready(env_name):
    return bool(
        os.getenv("RUN_ASR_PROVIDER_INTEGRATION", "").strip() == "1"
        and os.getenv(env_name, "").strip()
    )


class ASRProviderIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(
        integration_ready("ASSEMBLYAI_API_KEY"),
        "Set RUN_ASR_PROVIDER_INTEGRATION=1 and ASSEMBLYAI_API_KEY to run this test.",
    )
    async def test_assemblyai_transcribes_telephony_audio(self):
        await self._assert_transcription("assemblyai", "ASSEMBLYAI_API_KEY")

    @unittest.skipUnless(
        integration_ready("SPEECHMATICS_API_KEY"),
        "Set RUN_ASR_PROVIDER_INTEGRATION=1 and SPEECHMATICS_API_KEY to run this test.",
    )
    async def test_speechmatics_transcribes_telephony_audio(self):
        await self._assert_transcription("speechmatics", "SPEECHMATICS_API_KEY")

    @unittest.skipUnless(
        integration_ready("SONIOX_API_KEY"),
        "Set RUN_ASR_PROVIDER_INTEGRATION=1 and SONIOX_API_KEY to run this test.",
    )
    async def test_soniox_transcribes_telephony_audio(self):
        await self._assert_transcription("soniox", "SONIOX_API_KEY")

    async def _assert_transcription(self, provider, env_name):
        with patch.dict(os.environ, {env_name: os.getenv(env_name, "")}):
            service = create_stt_service(
                {
                    "provider": provider,
                    "locale": os.getenv("ASR_TEST_LOCALE", "en-US"),
                }
            )
        task_manager = TaskManager()
        task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
        clock = SystemClock()
        clock.start()
        await service.setup(
            FrameProcessorSetup(clock=clock, task_manager=task_manager)
        )

        transcripts = []
        final_received = asyncio.Event()
        original_push_frame = service.push_frame

        async def record_frame(frame, direction=FrameDirection.DOWNSTREAM):
            if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
                text = getattr(frame, "text", "").strip()
                if text:
                    transcripts.append(text)
                    if isinstance(frame, TranscriptionFrame):
                        final_received.set()
            await original_push_frame(frame, direction)

        service.push_frame = record_frame
        try:
            await service.process_frame(
                StartFrame(enable_metrics=True),
                FrameDirection.DOWNSTREAM,
            )
            pcm = _load_8khz_pcm(TEST_AUDIO_PATH)
            chunk_size = 8000 * 2 * 20 // 1000
            for offset in range(0, len(pcm), chunk_size):
                await service.process_frame(
                    InputAudioRawFrame(
                        audio=pcm[offset : offset + chunk_size],
                        sample_rate=8000,
                        num_channels=1,
                    ),
                    FrameDirection.DOWNSTREAM,
                )
            await service.process_frame(
                VADUserStoppedSpeakingFrame(stop_secs=0.5),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.wait_for(final_received.wait(), timeout=45)
            self.assertTrue(any(text.strip() for text in transcripts))
        finally:
            await service.stop(EndFrame())
            await service.cleanup()


def _load_8khz_pcm(path):
    with wave.open(str(path), "rb") as audio_file:
        sample_rate = audio_file.getframerate()
        channels = audio_file.getnchannels()
        sample_width = audio_file.getsampwidth()
        samples = audio_file.readframes(-1)
    if sample_width != 2:
        raise ValueError("Integration audio must be PCM16")
    audio = np.frombuffer(samples, dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    audio = audio.astype(np.float32) / 32768.0
    if sample_rate != 8000:
        audio = soxr.resample(audio, sample_rate, 8000)
    audio = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
    return audio.tobytes()


if __name__ == "__main__":
    unittest.main()
