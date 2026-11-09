import asyncio
import logging

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.bots.realtime.utils import _summarize_for_log


class RealtimeTranscriptContextPersister(FrameProcessor):
    """Observes final realtime user transcripts without writing duplicate context."""

    def __init__(self, context: LLMContext, on_transcription=None):
        super().__init__()
        self._context = context
        self._on_transcription = on_transcription
        self._last_persisted_text = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            text = (frame.text or "").strip()
            if text and text != self._last_persisted_text:
                logging.debug(
                    "Observed realtime user transcript: %r",
                    _summarize_for_log(text),
                )
                self._last_persisted_text = text
                if self._on_transcription:
                    result = self._on_transcription(text)
                    if asyncio.iscoroutine(result):
                        await result

        await self.push_frame(frame, direction)
