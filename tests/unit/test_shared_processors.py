import unittest
from types import SimpleNamespace

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.events import (
    VoicemailDetectedEvent,
    VoicemailDetectedFrame,
)
from collection_agents.processors.common import (
    HangupState,
    WordAwareSanitizer,
)
from collection_agents.processors.voicemail_detector import VoicemailDetector


class RecordingPushMixin:
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append((frame, direction))


class RecordingVoicemailDetector(RecordingPushMixin, VoicemailDetector):
    pass


class RecordingWordAwareSanitizer(RecordingPushMixin, WordAwareSanitizer):
    pass


def transcription(text, *, result=None):
    return TranscriptionFrame(
        text=text,
        user_id="user",
        timestamp="2026-01-01T00:00:00Z",
        result=result,
    )


class VoicemailDetectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_emits_detection_event(self):
        detector = RecordingVoicemailDetector()

        await detector.process_frame(
            TextFrame("Please leave a message after the tone"),
            FrameDirection.DOWNSTREAM,
        )

        self.assertEqual(len(detector.pushed), 1)
        frame, _ = detector.pushed[0]
        self.assertIsInstance(frame, VoicemailDetectedFrame)
        self.assertIn("leave a message", frame.event.transcript_text)
        self.assertIn("leave a message", frame.event.matched_text)
        self.assertEqual(frame.event.source, "standard")
        self.assertIn("leave a message", frame.event.trigger_text)
        self.assertFalse(hasattr(frame.event, "prompt"))

    async def test_realtime_only_detects_upstream_and_emits_event(self):
        events = []
        detector = RecordingVoicemailDetector(
            mode="realtime",
            on_detected=events.append,
        )
        downstream = transcription("Please leave a message")

        await detector.process_frame(downstream, FrameDirection.DOWNSTREAM)
        self.assertEqual(events, [])
        self.assertEqual(detector.pushed, [(downstream, FrameDirection.DOWNSTREAM)])

        detector.pushed.clear()
        await detector.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await detector.process_frame(
            transcription(
                "Please leave a message after the tone",
                result=SimpleNamespace(item_id="item-123"),
            ),
            FrameDirection.UPSTREAM,
        )

        self.assertEqual(len(events), 1)
        self.assertIsInstance(events[0], VoicemailDetectedEvent)
        self.assertEqual(events[0].item_id, "item-123")
        self.assertEqual(events[0].source, "realtime")
        self.assertIn("leave a message", events[0].transcript_text)
        self.assertEqual(
            [type(frame) for frame, _ in detector.pushed],
            [BotStartedSpeakingFrame],
        )

    async def test_realtime_without_event_callback_emits_frame_only(self):
        detector = RecordingVoicemailDetector(
            mode="realtime",
        )

        await detector.process_frame(
            transcription("Please leave a message"),
            FrameDirection.UPSTREAM,
        )

        self.assertEqual(len(detector.pushed), 1)
        frame, direction = detector.pushed[0]
        self.assertEqual(direction, FrameDirection.UPSTREAM)
        self.assertIsInstance(frame, VoicemailDetectedFrame)
        self.assertEqual(frame.event.source, "realtime")


class WordAwareSanitizerTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_buffers_text_and_marks_hangup(self):
        state = HangupState()
        sanitizer = RecordingWordAwareSanitizer(None, None, None, state)

        await sanitizer.process_frame(TextFrame("Thank you. Goodbye"), FrameDirection.DOWNSTREAM)
        self.assertFalse(state.is_hanging_up)
        await sanitizer.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

        self.assertTrue(state.is_hanging_up)
        self.assertTrue(state.final_phrase_detected)
        self.assertEqual(len(sanitizer.pushed), 2)

    async def test_realtime_ignores_duplicate_llm_text_view(self):
        state = HangupState()
        sanitizer = RecordingWordAwareSanitizer(None, None, None, state, mode="realtime")

        await sanitizer.process_frame(LLMTextFrame("Goodbye"), FrameDirection.DOWNSTREAM)
        await sanitizer.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        self.assertFalse(state.is_hanging_up)
        self.assertEqual(sanitizer._buffer, "")

        await sanitizer.process_frame(
            TTSTextFrame("Goodbye", aggregated_by="sentence"),
            FrameDirection.DOWNSTREAM,
        )
        await sanitizer.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
        self.assertTrue(state.is_hanging_up)
        self.assertTrue(state.final_phrase_detected)
        self.assertEqual(sanitizer._buffer, "")

    async def test_overflow_policies_remain_mode_specific(self):
        standard = RecordingWordAwareSanitizer(None, None, None)
        realtime = RecordingWordAwareSanitizer(None, None, None, mode="realtime")
        text = "word " * 199 + "Goodbye"

        await standard.process_frame(TextFrame(text), FrameDirection.DOWNSTREAM)
        self.assertTrue(standard._is_hanging_up)
        self.assertEqual(standard._buffer, "")

        await realtime.process_frame(
            TTSTextFrame(text, aggregated_by="sentence"),
            FrameDirection.DOWNSTREAM,
        )
        self.assertFalse(realtime._is_hanging_up)
        self.assertEqual(realtime._buffer, text[-1000:])


if __name__ == "__main__":
    unittest.main()
