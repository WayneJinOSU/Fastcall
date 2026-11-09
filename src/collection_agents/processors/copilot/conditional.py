import logging

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    StartFrame,
    SystemFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


class ConditionalProcessor(FrameProcessor):
    """Drops non-system frames while the supplied condition is true."""

    def __init__(self, condition_func, **kwargs):
        super().__init__(**kwargs)
        self._condition_func = condition_func

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        system_frames = (
            StartFrame | EndFrame | CancelFrame | SystemFrame
        )
        if isinstance(frame, system_frames):
            await self.push_frame(frame, direction)
            return

        if self._condition_func():
            logging.debug(
                f"{self.name}: Dropping frame {frame.__class__.__name__}"
            )
            return

        await self.push_frame(frame, direction)
