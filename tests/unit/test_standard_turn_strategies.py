import asyncio
import unittest

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    InterruptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
from pipecat.processors.frame_processor import FrameDirection
from pipecat.turns.user_mute import AlwaysUserMuteStrategy
from pipecat.turns.user_turn_controller import UserTurnController
from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

from collection_agents.bots.standard.orchestrator import _build_user_aggregator_params


class RecordingUserAggregator(LLMUserAggregator):
    def __init__(self, *args, **kwargs):
        self.pushed = []
        super().__init__(*args, **kwargs)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append((frame, direction))


class StandardTurnStrategyTests(unittest.IsolatedAsyncioTestCase):
    def _task_manager(self):
        task_manager = TaskManager()
        task_manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
        return task_manager

    async def _controller(self, started):
        params = _build_user_aggregator_params(allow_interruptions=True)
        controller = UserTurnController(
            user_turn_strategies=params.user_turn_strategies,
            user_turn_stop_timeout=params.user_turn_stop_timeout,
        )
        await controller.setup(self._task_manager())
        self.addAsyncCleanup(controller.cleanup)

        @controller.event_handler("on_user_turn_started")
        async def _on_started(controller, strategy, params):
            started.append(params)

        return controller

    async def test_vad_and_empty_transcript_do_not_start_turn(self):
        started = []
        controller = await self._controller(started)

        await controller.process_frame(VADUserStartedSpeakingFrame(start_secs=0.3))
        await controller.process_frame(
            InterimTranscriptionFrame(
                text=" ",
                user_id="user",
                timestamp="2026-01-01T00:00:00Z",
            )
        )

        self.assertEqual(started, [])

    async def test_interim_transcript_starts_interruptible_turn(self):
        started = []
        controller = await self._controller(started)

        await controller.process_frame(
            InterimTranscriptionFrame(
                text="si",
                user_id="user",
                timestamp="2026-01-01T00:00:00Z",
            )
        )

        self.assertEqual(len(started), 1)
        self.assertTrue(started[0].enable_interruptions)

    async def test_final_transcript_starts_interruptible_turn(self):
        started = []
        controller = await self._controller(started)

        await controller.process_frame(VADUserStartedSpeakingFrame(start_secs=0.3))
        await controller.process_frame(
            TranscriptionFrame(
                text="hello",
                user_id="user",
                timestamp="2026-01-01T00:00:00Z",
            )
        )

        self.assertEqual(len(started), 1)
        self.assertTrue(started[0].enable_interruptions)

    async def test_aggregator_interrupts_only_after_transcript(self):
        aggregator = RecordingUserAggregator(
            context=LLMContext([]),
            params=_build_user_aggregator_params(allow_interruptions=True),
        )
        await aggregator._user_turn_controller.setup(self._task_manager())
        self.addAsyncCleanup(aggregator._user_turn_controller.cleanup)
        aggregator._allow_interruptions = True

        await aggregator.process_frame(
            VADUserStartedSpeakingFrame(start_secs=0.3),
            FrameDirection.DOWNSTREAM,
        )
        self.assertFalse(
            any(isinstance(frame, InterruptionFrame) for frame, _ in aggregator.pushed)
        )

        await aggregator.process_frame(
            InterimTranscriptionFrame(
                text="si",
                user_id="user",
                timestamp="2026-01-01T00:00:00Z",
            ),
            FrameDirection.DOWNSTREAM,
        )
        self.assertTrue(
            any(isinstance(frame, InterruptionFrame) for frame, _ in aggregator.pushed)
        )

    def test_disabled_interruptions_mute_user_during_bot_speech(self):
        enabled = _build_user_aggregator_params(allow_interruptions=True)
        disabled = _build_user_aggregator_params(allow_interruptions=False)

        self.assertFalse(
            any(isinstance(s, AlwaysUserMuteStrategy) for s in enabled.user_mute_strategies)
        )
        self.assertTrue(
            any(isinstance(s, AlwaysUserMuteStrategy) for s in disabled.user_mute_strategies)
        )


if __name__ == "__main__":
    unittest.main()
