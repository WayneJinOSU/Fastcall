import asyncio
import logging
import time
from collections import deque

_logger = logging.getLogger(__name__)

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMAssistantPushAggregationFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.processors.common.hangup import HangupState
from pipecat.utils.string import TextPartForConcatenation, concatenate_aggregated_text

class OpeningSilenceTimeoutProcessor(FrameProcessor):
    """Hangs up only when BOTH conditions are met (AND logic):
      - No UserStartedSpeakingFrame within `opening_timeout` seconds of the first BotStartedSpeakingFrame
      - No UserStartedSpeakingFrame within `post_stop_timeout` seconds of the first BotStoppedSpeakingFrame

    Either timer firing alone is not enough; both must fire before any user speech is detected.
    Any UserStartedSpeakingFrame cancels both timers and disarms the processor permanently.
    """

    def __init__(self, *, hangup_cb, opening_timeout: float = 15.0, post_stop_timeout: float = 10.0):
        super().__init__()
        self._hangup_cb = hangup_cb
        self._opening_timeout = opening_timeout
        self._post_stop_timeout = post_stop_timeout
        self._armed = False
        self._opening_task = None
        self._post_stop_task = None
        self._opening_fired = False
        self._post_stop_fired = False
        self._hangup_direction = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (EndFrame, CancelFrame)):
            self._disarm()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStartedSpeakingFrame) and not self._armed:
            self._armed = True
            self._hangup_direction = direction
            self._opening_task = asyncio.ensure_future(self._opening_timeout_loop())

        elif self._armed and isinstance(frame, BotStoppedSpeakingFrame) and self._post_stop_task is None:
            self._post_stop_task = asyncio.ensure_future(self._post_stop_timeout_loop())

        elif self._armed and isinstance(frame, UserStartedSpeakingFrame):
            self._disarm()

        await self.push_frame(frame, direction)

    def _disarm(self):
        self._armed = False
        for task in (self._opening_task, self._post_stop_task):
            if task and not task.done():
                task.cancel()
        self._opening_task = None
        self._post_stop_task = None

    async def cleanup(self):
        # EndFrame may be pushed mid-pipeline (e.g. by GracefulHangupController)
        # and never reach this processor, leaving the timer tasks running as
        # zombies. cleanup() is guaranteed to be called on pipeline teardown.
        await super().cleanup()
        self._disarm()

    async def _maybe_hangup(self, label: str):
        if self._opening_fired and self._post_stop_fired:
            logging.warning(
                "[OpeningSilenceTimeoutProcessor] Both timers fired (opening=%.1fs, post_stop=%.1fs), triggering hangup",
                self._opening_timeout,
                self._post_stop_timeout,
            )
            if self._hangup_cb:
                result = self._hangup_cb()
                if asyncio.iscoroutine(result):
                    await result
            await self.push_frame(EndFrame(), self._hangup_direction)

    async def _opening_timeout_loop(self):
        try:
            await asyncio.sleep(self._opening_timeout)
            if self._armed:
                self._opening_fired = True
                await self._maybe_hangup("opening")
        except asyncio.CancelledError:
            raise

    async def _post_stop_timeout_loop(self):
        try:
            await asyncio.sleep(self._post_stop_timeout)
            if self._armed:
                self._post_stop_fired = True
                await self._maybe_hangup("post_stop")
        except asyncio.CancelledError:
            raise
