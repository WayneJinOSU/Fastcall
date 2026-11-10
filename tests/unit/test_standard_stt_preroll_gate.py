import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InputAudioRawFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.processors.standard.stt_preroll_gate import STTPrerollGate


class RecordingSTTPrerollGate(STTPrerollGate):
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append((frame, direction))


class STTPrerollGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_start_during_bot_speech_unmutes_user_audio(self):
        gate = RecordingSTTPrerollGate()
        bot_started = BotStartedSpeakingFrame()
        vad_started = VADUserStartedSpeakingFrame(start_secs=0.3)
        audio = InputAudioRawFrame(
            audio=b"\x00\x00" * 160,
            sample_rate=8000,
            num_channels=1,
        )

        await gate.process_frame(bot_started, FrameDirection.DOWNSTREAM)
        self.assertTrue(gate._muted)

        await gate.process_frame(vad_started, FrameDirection.DOWNSTREAM)
        await gate.process_frame(audio, FrameDirection.DOWNSTREAM)

        pushed_frames = [frame for frame, _ in gate.pushed]
        self.assertIn(bot_started, pushed_frames)
        self.assertIn(vad_started, pushed_frames)
        self.assertIn(audio, pushed_frames)
        self.assertFalse(gate._muted)

        await gate.process_frame(
            UserStoppedSpeakingFrame(),
            FrameDirection.DOWNSTREAM,
        )
        self.assertTrue(gate._muted)
        await gate.cleanup()


if __name__ == "__main__":
    unittest.main()
