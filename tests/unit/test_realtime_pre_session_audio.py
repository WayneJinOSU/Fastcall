import unittest
from collections import deque

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

from collection_agents.bots.realtime.azure_service import (
    AzureRealtimeResponseCoordinator,
)


class RealtimePreSessionAudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_audio_is_flushed_after_session_ready(self):
        service = object.__new__(AzureRealtimeResponseCoordinator)
        service._pending_input_audio = deque()
        service._pending_input_audio_ms = 0.0
        service._input_ready_for_live = False
        service._has_uncommitted_input_audio = False
        service._uncommitted_input_audio_ms = 0.0

        sent_frames = []

        async def send_user_audio(frame):
            sent_frames.append(frame)

        service._send_user_audio = send_user_audio
        frame = InputAudioRawFrame(
            audio=b"\x00\x00" * 160,
            sample_rate=8000,
            num_channels=1,
        )

        await service.process_frame(frame, FrameDirection.DOWNSTREAM)

        self.assertEqual(sent_frames, [])
        self.assertEqual(list(service._pending_input_audio), [(frame, 20.0)])

        await service._flush_pending_input_audio()

        self.assertEqual(sent_frames, [frame])
        self.assertTrue(service._input_ready_for_live)
        self.assertTrue(service._has_uncommitted_input_audio)
        self.assertAlmostEqual(service._uncommitted_input_audio_ms, 20.0)


if __name__ == "__main__":
    unittest.main()
