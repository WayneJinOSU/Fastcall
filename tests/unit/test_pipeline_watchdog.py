import unittest
from types import SimpleNamespace

from pipecat.frames.frames import TTSAudioRawFrame

from collection_agents.bots.standard import PipelineFlowWatchdog


class PipelineFlowWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sink = object()
        self.watchdog = PipelineFlowWatchdog(sink=self.sink, timeout_sec=10)
        self.watchdog.rearm()
        self.audio = TTSAudioRawFrame(
            audio=b"\x00\x00" * 160,
            sample_rate=8000,
            num_channels=1,
        )

    async def asyncTearDown(self):
        await self.watchdog.cleanup()

    async def test_audio_reaching_transport_output_disarms_watchdog(self):
        await self.watchdog.on_push_frame(
            SimpleNamespace(frame=self.audio, destination=self.sink)
        )
        self.assertFalse(self.watchdog._armed)
        self.assertIsNone(self.watchdog._watch_task)

    async def test_audio_before_sink_does_not_disarm_watchdog(self):
        await self.watchdog.on_push_frame(
            SimpleNamespace(frame=self.audio, destination=object())
        )
        self.assertTrue(self.watchdog._armed)

        await self.watchdog.on_push_frame(
            SimpleNamespace(frame=self.audio, destination=None)
        )
        self.assertTrue(self.watchdog._armed)


if __name__ == "__main__":
    unittest.main()
