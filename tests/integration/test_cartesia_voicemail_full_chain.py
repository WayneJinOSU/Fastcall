import asyncio
import os
import tempfile
import time
import unittest
import wave
from pathlib import Path

from dotenv import load_dotenv
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    EndFrame,
    InputAudioRawFrame,
    LLMMessagesAppendFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessorSetup,
)
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

from collection_agents.events import VoicemailDetectedFrame
from collection_agents.handlers.standard.voicemail import StandardVoicemailHandler
from collection_agents.processors.common.voicemail_detector import VoicemailDetector
from collection_agents.services.audio_assets import (
    DEFAULT_CARTESIA_VOICE_ID,
    DEFAULT_MODEL,
    _generate_cartesia_wav_bytes,
)
from collection_agents.services.stt_local import VoskSTTService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)

RUN_INTEGRATION = os.getenv("RUN_CARTESIA_INTEGRATION", "").strip() == "1"
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "").strip()
VOSK_MODEL_PATH = Path(os.getenv("VOSK_MODEL_PATH", "")).expanduser()
INTEGRATION_READY = bool(
    RUN_INTEGRATION
    and CARTESIA_API_KEY
    and str(VOSK_MODEL_PATH) not in {"", "."}
    and VOSK_MODEL_PATH.is_dir()
)

VOICEMAIL_TEXT = (
    "Hola. En este momento no puedo atender el teléfono. "
    "Por favor, deje su mensaje después del tono."
)


class RecordingVoskSTTService(VoskSTTService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.final_transcripts = []
        self.interim_transcripts = []

    async def push_frame(
        self,
        frame,
        direction=FrameDirection.DOWNSTREAM,
    ):
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self.final_transcripts.append(frame.text.strip())
        elif type(frame).__name__ == "InterimTranscriptionFrame":
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                self.interim_transcripts.append(text)

    async def wait_for_final(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.final_transcripts:
                return self.final_transcripts[-1]
            await asyncio.sleep(0.02)
        return None


class RecordingVoicemailDetector(VoicemailDetector):
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append((frame, direction))


class RecordingStandardVoicemailHandler(StandardVoicemailHandler):
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)


@unittest.skipUnless(
    INTEGRATION_READY,
    "Set RUN_CARTESIA_INTEGRATION=1 and VOSK_MODEL_PATH to run this test.",
)
class CartesiaVoicemailFullChainTests(unittest.IsolatedAsyncioTestCase):
    async def test_cartesia_audio_reaches_voicemail_handler(self):
        audio_bytes = await _generate_cartesia_wav_bytes(
            text=VOICEMAIL_TEXT,
            voice_id=DEFAULT_CARTESIA_VOICE_ID,
            model=DEFAULT_MODEL,
            locale="es-MX",
            sample_rate=8000,
        )
        self.assertGreater(len(audio_bytes), 1000)

        output_path = Path(
            os.getenv(
                "CARTESIA_TEST_AUDIO_PATH",
                str(Path(tempfile.gettempdir()) / "cartesia-voicemail-full-chain.wav"),
            )
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)

        with wave.open(str(output_path), "rb") as audio_file:
            self.assertEqual(audio_file.getframerate(), 8000)
            self.assertEqual(audio_file.getnchannels(), 1)
            self.assertEqual(audio_file.getsampwidth(), 2)
            pcm = audio_file.readframes(-1)

        transcript = await self._transcribe(pcm)
        self.assertIsNotNone(transcript)
        normalized_transcript = transcript.lower()
        self.assertTrue(
            any(
                phrase in normalized_transcript
                for phrase in (
                    "no puedo atender",
                    "deje su mensaje",
                    "después del tono",
                    "despues del tono",
                )
            ),
            f"Voicemail pattern not recovered from transcript: {transcript!r}",
        )

        detector = RecordingVoicemailDetector()
        await detector.process_frame(
            TextFrame(transcript),
            FrameDirection.DOWNSTREAM,
        )
        self.assertEqual(len(detector.pushed), 1)
        detected_frame, direction = detector.pushed[0]
        self.assertEqual(direction, FrameDirection.DOWNSTREAM)
        self.assertIsInstance(detected_frame, VoicemailDetectedFrame)
        self.assertEqual(detected_frame.event.source, "standard")
        self.assertIn(
            detected_frame.event.matched_text.lower(),
            normalized_transcript,
        )

        handler = RecordingStandardVoicemailHandler(
            mode="legacy_llm",
            locale="es-MX",
        )
        await handler.process_frame(
            detected_frame,
            FrameDirection.DOWNSTREAM,
        )

        prompts = [
            frame
            for frame in handler.pushed
            if isinstance(frame, LLMMessagesAppendFrame)
        ]
        self.assertEqual(len(prompts), 1)
        self.assertTrue(prompts[0].run_llm)
        prompt = prompts[0].messages[0]["content"]
        self.assertIn("Buzón de voz detectado", prompt)
        self.assertIn("chao", prompt)

        print(f"\nCartesia audio: {output_path}")
        print(f"Cartesia duration: {len(pcm) / 16000:.2f}s")
        print(f"Vosk transcript: {transcript}")
        print(f"Detected: {detected_frame.event.matched_text!r}")
        print("Voicemail handler prompt: emitted")

    @staticmethod
    async def _transcribe(pcm: bytes) -> str | None:
        stt = RecordingVoskSTTService(
            model_path=str(VOSK_MODEL_PATH),
            sample_rate=8000,
        )
        task_manager = TaskManager()
        task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
        clock = SystemClock()
        clock.start()
        await stt.setup(FrameProcessorSetup(clock=clock, task_manager=task_manager))

        try:
            await stt.process_frame(
                StartFrame(enable_metrics=True),
                FrameDirection.DOWNSTREAM,
            )
            await asyncio.wait_for(stt._ready.wait(), timeout=30)
            await stt.process_frame(
                VADUserStartedSpeakingFrame(),
                FrameDirection.DOWNSTREAM,
            )

            chunk_size = 8000 * 2 * 20 // 1000
            for offset in range(0, len(pcm), chunk_size):
                await stt.process_frame(
                    InputAudioRawFrame(
                        audio=pcm[offset : offset + chunk_size],
                        sample_rate=8000,
                        num_channels=1,
                    ),
                    FrameDirection.DOWNSTREAM,
                )

            await stt.process_frame(
                VADUserStoppedSpeakingFrame(stop_secs=0.5),
                FrameDirection.DOWNSTREAM,
            )
            return await stt.wait_for_final()
        finally:
            await stt.stop(EndFrame())
            await stt.cleanup()


if __name__ == "__main__":
    unittest.main()
