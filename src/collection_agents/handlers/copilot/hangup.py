import logging
import re

from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    StartFrame,
    SystemFrame,
    TextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


class CopilotWordAwareHangupProcessor(FrameProcessor):
    """Starts the hangup flow only after the model has produced an ending phrase."""

    def __init__(self, *, on_hangup_requested, on_hangup_started=None, **kwargs):
        super().__init__(**kwargs)
        self._on_hangup_requested = on_hangup_requested
        self._on_hangup_started = on_hangup_started
        self._buffer = ""
        self._is_hanging_up = False
        self._is_ended = False
        self._endcall_regex = re.compile(
            r"(?i)("
            r"\b(?:goodbye|bye|bye-bye|see you|have a great day|have a good one|"
            r"adi[oó]s|hasta\s+(?:luego|mañana|pronto)|nos\s+vemos|chao|chau|cu[ií]date|"
            r"paalam|hanggang\s+sa\s+muli|ingat\s+ka|"
            r"que\s+tenga\s+(?:un\s+)?(?:lindo|buen|excelente)\s+d[ií]a)\b|"
            r"再见|拜拜|挂了|"
            r"\[\[HANGUP\]\]"
            r")"
        )

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if self._is_hanging_up:
            if isinstance(frame, (TextFrame, InterruptionFrame)):
                return
            if isinstance(frame, EndFrame):
                self._is_ended = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            self._buffer += frame.text
            await self.push_frame(frame, direction)
            self._maybe_start_hangup()
            return

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            self._maybe_start_hangup()
            self._buffer = ""
            if isinstance(frame, EndFrame):
                self._is_ended = True
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _maybe_start_hangup(self):
        clean_text = self._buffer.strip()
        if not clean_text or self._is_hanging_up:
            return
        if self._endcall_regex.search(clean_text):
            logging.info(f"Detected copilot end phrase, start graceful hangup: {clean_text[:120]!r}")
            self._is_hanging_up = True
            if self._on_hangup_started:
                self._on_hangup_started()
            self.create_task(self._perform_hangup(), "copilot_graceful_hangup")

    async def _perform_hangup(self):
        try:
            await self._on_hangup_requested()
        except Exception as e:
            logging.error(f"Copilot graceful hangup error: {e}")
