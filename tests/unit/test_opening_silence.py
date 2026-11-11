import asyncio
import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.handlers.standard.opening_silence import OpeningSilenceHandler


class RecordingOpeningSilenceHandler(OpeningSilenceHandler):
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append((frame, direction))


class OpeningSilenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_activity_permanently_disarms_hangup(self):
        hangup_calls = []
        handler = RecordingOpeningSilenceHandler(
            hangup_cb=lambda: hangup_calls.append("hangup"),
            opening_timeout=0.01,
            post_stop_timeout=0.01,
        )

        await handler.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await handler.process_frame(
            VADUserStartedSpeakingFrame(start_secs=0.3),
            FrameDirection.DOWNSTREAM,
        )
        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.04)

        # A later bot turn must not re-arm the idle-hangup timer.
        await handler.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.04)

        self.assertEqual(hangup_calls, [])
        self.assertFalse(any(isinstance(frame, EndFrame) for frame, _ in handler.pushed))
        await handler.cleanup()

    async def test_transcription_also_disarms_hangup(self):
        hangup_calls = []
        handler = RecordingOpeningSilenceHandler(
            hangup_cb=lambda: hangup_calls.append("hangup"),
            opening_timeout=0.01,
            post_stop_timeout=0.01,
        )
        handler._armed = True

        await handler.process_frame(
            TranscriptionFrame(
                text="hello",
                user_id="user",
                timestamp="2026-11-10T00:00:00Z",
            ),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0.04)

        self.assertEqual(hangup_calls, [])
        await handler.cleanup()

    async def test_true_opening_silence_still_hangs_up(self):
        hangup_calls = []
        handler = RecordingOpeningSilenceHandler(
            hangup_cb=lambda: hangup_calls.append("hangup"),
            opening_timeout=0.01,
            post_stop_timeout=0.01,
        )

        await handler.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(0.05)

        self.assertEqual(hangup_calls, ["hangup"])
        self.assertTrue(any(isinstance(frame, EndFrame) for frame, _ in handler.pushed))
        await handler.cleanup()


if __name__ == "__main__":
    unittest.main()
