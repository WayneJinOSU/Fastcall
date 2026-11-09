import logging
import time

_logger = logging.getLogger(__name__)

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class TranscriptionLogger(FrameProcessor):
    """Logs transcription frames and emits `on_transcription` events."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._register_event_handler("on_transcription")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            logging.debug(f"Transcription: {frame.text}")
            await self._call_event_handler(
                "on_transcription",
                text=frame.text,
                is_final=True,
            )

        await self.push_frame(frame, direction)

class FrameLogger(FrameProcessor):
    """Periodic frame-flow summary logger.

    This logger is designed for production debugging:
    - low noise (interval summary instead of per-frame spam)
    - visible in normal INFO-level logs
    """

    def __init__(self, interval_sec=10.0, log_level=logging.INFO, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._interval_sec = interval_sec
        self._log_level = log_level
        self._last_log_time = time.monotonic()
        self._total = 0
        self._downstream = 0
        self._upstream = 0
        self._by_type = {}

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        frame_type = type(frame).__name__
        self._total += 1
        if direction == FrameDirection.DOWNSTREAM:
            self._downstream += 1
        else:
            self._upstream += 1
        self._by_type[frame_type] = self._by_type.get(frame_type, 0) + 1

        now = time.monotonic()
        if now - self._last_log_time >= self._interval_sec:
            top_types = sorted(self._by_type.items(), key=lambda item: item[1], reverse=True)[:5]
            top_text = ", ".join(f"{name}:{count}" for name, count in top_types) if top_types else "none"
            logging.log(
                self._log_level,
                "FrameLogger summary: interval=%.1fs total=%d downstream=%d upstream=%d top=[%s]",
                now - self._last_log_time,
                self._total,
                self._downstream,
                self._upstream,
                top_text,
            )
            self._last_log_time = now
            self._total = 0
            self._downstream = 0
            self._upstream = 0
            self._by_type.clear()

        await self.push_frame(frame, direction)
