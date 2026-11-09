import unittest
from types import SimpleNamespace

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.processors.common import (
    HangupState,
    VoicemailDetectedFrame,
    VoicemailDetector,
    WordAwareSanitizer,
)


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
        detector = RecordingVoicemailDetector(language="fil-PH")

        await detector.process_frame(
            TextFrame("Please leave a message after the tone"),
            FrameDirection.DOWNSTREAM,
        )

        self.assertEqual(len(detector.pushed), 1)
        event, _ = detector.pushed[0]
        self.assertIsInstance(event, VoicemailDetectedFrame)
        self.assertIn("leave a message", event.transcript_text)
        self.assertIn("Paalam", event.prompt)

    async def test_realtime_only_detects_upstream_and_emits_event(self):
        events = []
        detector = RecordingVoicemailDetector(
            mode="realtime",
            voice_assistant_cb=events.append,
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
        self.assertEqual(events[0]["item_id"], "item-123")
        self.assertEqual(events[0]["classification_detail"], "voicemail")
        self.assertEqual(events[0]["action"], "detected")
        self.assertEqual(
            [type(frame) for frame, _ in detector.pushed],
            [BotStartedSpeakingFrame],
        )

    async def test_realtime_without_event_callback_uses_realtime_prompt(self):
        detected = []
        detector = RecordingVoicemailDetector(
            mode="realtime",
            language="en-US",
            detected_cb=lambda: detected.append(True),
        )

        await detector.process_frame(
            transcription("Please leave a message"),
            FrameDirection.UPSTREAM,
        )

        self.assertEqual(detected, [True])
        self.assertEqual(len(detector.pushed), 1)
        prompt, direction = detector.pushed[0]
        self.assertEqual(direction, FrameDirection.UPSTREAM)
        self.assertIsInstance(prompt, LLMMessagesAppendFrame)
        self.assertTrue(prompt.run_llm)
        self.assertIn("Leave the debt-collection voicemail immediately", prompt.messages[0]["content"])


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
