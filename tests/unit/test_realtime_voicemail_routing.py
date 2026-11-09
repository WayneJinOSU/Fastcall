import unittest
from types import SimpleNamespace

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.processors.common.voicemail_detector import VoicemailDetector
from collection_agents.processors.realtime.transcription_monitor import RealtimeInputTranscriptionMonitor


class RecordingVoicemailDetector(VoicemailDetector):
    def __init__(self):
        self.events = []
        super().__init__(
            mode="realtime",
            on_detected=self.events.append,
        )

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        return None


class ChainedRealtimeInputMonitor(RealtimeInputTranscriptionMonitor):
    def __init__(self, downstream, **kwargs):
        super().__init__(**kwargs)
        self._downstream = downstream

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        await self._downstream.process_frame(frame, direction)


def transcription_frame(frame_type, text, item_id):
    return frame_type(
        text=text,
        user_id="user-1",
        timestamp="2026-11-09T21:20:00+08:00",
        result=SimpleNamespace(item_id=item_id),
    )


class RealtimeVoicemailRoutingTests(unittest.IsolatedAsyncioTestCase):
    def build_chain(self):
        detector = RecordingVoicemailDetector()
        monitor_events = []

        async def on_voice_assistant(event):
            monitor_events.append(event)

        monitor = ChainedRealtimeInputMonitor(
            downstream=detector,
            monitor_state={},
            voice_assistant_cb=on_voice_assistant,
        )
        return monitor, monitor_events, detector

    async def test_interim_voicemail_is_handled_before_detector(self):
        monitor, monitor_events, detector = self.build_chain()

        await monitor.process_frame(
            transcription_frame(
                InterimTranscriptionFrame,
                "Hola, en este momento no puedo atender.",
                "item-interim",
            ),
            FrameDirection.UPSTREAM,
        )

        self.assertEqual(len(monitor_events), 1)
        self.assertEqual(monitor_events[0]["item_id"], "item-interim")
        self.assertEqual(
            monitor_events[0]["classification_detail"],
            "voicemail",
        )
        self.assertEqual(monitor_events[0]["action"], "suppressed")
        self.assertFalse(monitor_events[0]["is_final"])
        self.assertEqual(detector.events, [])

    async def test_final_only_voicemail_is_still_preempted_by_monitor(self):
        monitor, monitor_events, detector = self.build_chain()

        await monitor.process_frame(
            transcription_frame(
                TranscriptionFrame,
                "No puedo atender. Deje su mensaje después del tono.",
                "item-final",
            ),
            FrameDirection.UPSTREAM,
        )

        self.assertEqual(len(monitor_events), 1)
        self.assertEqual(monitor_events[0]["item_id"], "item-final")
        self.assertEqual(monitor_events[0]["action"], "suppressed")
        self.assertTrue(monitor_events[0]["is_final"])
        self.assertEqual(detector.events, [])


if __name__ == "__main__":
    unittest.main()
