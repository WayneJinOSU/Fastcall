import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMMessagesAppendFrame,
    OutputAudioRawFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.bots.standard.voicemail import (
    FixedVoicemailPlayer,
    StandardVoicemailHandler,
)
from collection_agents.processors.common import VoicemailDetectedFrame


class RecordingHandler(StandardVoicemailHandler):
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)


class VoicemailHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_message_splits_internally_and_completes(self):
        detected = []
        finalized = []
        completed = []
        handler = RecordingHandler(
            mode="fixed_message",
            on_detected=lambda: detected.append(True),
            finalize_stt_cb=lambda: finalized.append(True),
            fixed_text="First sentence. Payment reminder",
            fixed_done_cb=lambda: completed.append(True),
        )

        await handler.process_frame(
            VoicemailDetectedFrame(transcript_text="voicemail"),
            FrameDirection.DOWNSTREAM,
        )
        self.assertEqual(detected, [True])
        self.assertEqual(finalized, [True])
        self.assertIsInstance(handler.pushed[0], LLMMessagesAppendFrame)
        self.assertFalse(handler.pushed[0].run_llm)
        self.assertEqual(
            handler.pushed[0].messages[0]["content"],
            "First sentence. Payment reminder",
        )
        self.assertEqual(handler.pushed[1].text, "First sentence.")

        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        speech = [frame for frame in handler.pushed if isinstance(frame, TTSSpeakFrame)]
        self.assertEqual(speech[-1].text, "Payment reminder")
        self.assertEqual(completed, [])

        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        self.assertEqual(completed, [True])

    async def test_detection_while_bot_speaking_is_deferred(self):
        handler = RecordingHandler(mode="fixed_message", fixed_text="Reminder")
        await handler.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await handler.process_frame(VoicemailDetectedFrame(), FrameDirection.DOWNSTREAM)
        self.assertFalse(any(isinstance(frame, LLMMessagesAppendFrame) for frame in handler.pushed))

        await handler.process_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        self.assertTrue(any(isinstance(frame, LLMMessagesAppendFrame) for frame in handler.pushed))

    async def test_legacy_mode_uses_prompt_from_detection_event(self):
        handler = RecordingHandler(mode="legacy_llm")
        await handler.process_frame(
            VoicemailDetectedFrame(prompt="Leave a message"),
            FrameDirection.DOWNSTREAM,
        )
        self.assertEqual(len(handler.pushed), 1)
        self.assertIsInstance(handler.pushed[0], LLMMessagesAppendFrame)
        self.assertTrue(handler.pushed[0].run_llm)
        self.assertEqual(handler.pushed[0].messages[0]["content"], "Leave a message")

    async def test_hangup_mode_calls_instant_close(self):
        closed = []
        handler = RecordingHandler(
            mode="hangup",
            instant_close_cb=lambda: closed.append(True),
        )
        await handler.process_frame(VoicemailDetectedFrame(), FrameDirection.DOWNSTREAM)
        self.assertEqual(closed, [True])
        self.assertEqual(handler.pushed, [])

    async def test_cached_audio_is_used_for_remainder(self):
        audio = OutputAudioRawFrame(
            audio=b"\x00\x00" * 160,
            sample_rate=8000,
            num_channels=1,
        )
        completed = []
        player = FixedVoicemailPlayer(
            text="First sentence. Cached reminder",
            audio_lookup=lambda: audio,
            on_complete=lambda: completed.append(True),
        )
        emitted = []

        async def emit(frame, direction):
            emitted.append(frame)

        await player.play(emit, FrameDirection.DOWNSTREAM)
        await player.on_bot_stopped(emit, FrameDirection.DOWNSTREAM)
        self.assertIn(audio, emitted)
        await player.on_bot_stopped(emit, FrameDirection.DOWNSTREAM)
        self.assertEqual(completed, [True])
        await player.cleanup()


if __name__ == "__main__":
    unittest.main()
