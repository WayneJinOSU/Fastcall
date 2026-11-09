import time

from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class ContextMessageTimestampTracker(FrameProcessor):
    """Tracks per-message timestamps for LLMContext without mutating provider payload."""

    def __init__(self, context, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._context = context
        existing = getattr(context, "_message_timestamps_ms", None)
        self._timestamps = list(existing) if isinstance(existing, list) else []
        self._next_timestamp_ms = int(time.time() * 1000)
        self._sync()

    def _allocate_timestamp(self, now_ms: int) -> int:
        timestamp_ms = max(now_ms, self._next_timestamp_ms)
        self._next_timestamp_ms = timestamp_ms + 1
        return timestamp_ms

    @staticmethod
    def _parse_timestamp(value):
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _sync(self):
        messages = getattr(self._context, "messages", None)
        if not isinstance(messages, list):
            return

        now_ms = int(time.time() * 1000)

        while len(self._timestamps) < len(messages):
            message = messages[len(self._timestamps)]
            explicit_ts = None
            if isinstance(message, dict):
                explicit_ts = self._parse_timestamp(message.get("timestamp"))
            if explicit_ts is not None:
                timestamp_ms = max(explicit_ts, self._next_timestamp_ms)
                self._next_timestamp_ms = timestamp_ms + 1
            else:
                timestamp_ms = self._allocate_timestamp(now_ms)
            self._timestamps.append(timestamp_ms)

        if len(self._timestamps) > len(messages):
            self._timestamps = self._timestamps[: len(messages)]

        setattr(self._context, "_message_timestamps_ms", list(self._timestamps))

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self._sync()
        await self.push_frame(frame, direction)
