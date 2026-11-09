import logging

_logger = logging.getLogger(__name__)

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.processors.common.hangup import HangupState

class HangupGate(FrameProcessor):
    """拦截 hangup 触发后的 TranscriptionFrame，防止语音信箱持续发声驱动多余 LLM 调用。"""

    def __init__(self, hangup_state: HangupState):
        super().__init__()
        self._state = hangup_state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._state.is_hanging_up and not self._state.input_blocked:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            await self.push_frame(frame, direction)
            return

        if self._state.is_hanging_up and isinstance(frame, LLMMessagesAppendFrame):
            return

        if isinstance(
            frame,
            (
                InterruptionFrame,
                TextFrame,
                TranscriptionFrame,
                UserStartedSpeakingFrame,
                UserStoppedSpeakingFrame,
            ),
        ) or type(frame).__name__ == "InterimTranscriptionFrame":
            logging.debug(
                "[HangupGate] Dropping user input after voicemail/hangup triggered: %s",
                type(frame).__name__,
            )
            return

        if (self._state.block_ghost_llm or self._state.is_hanging_up) and isinstance(frame, LLMContextFrame):
            logging.debug(
                "[HangupGate] Dropping LLMContextFrame (hangup in progress)"
            )
            return

        await self.push_frame(frame, direction)
