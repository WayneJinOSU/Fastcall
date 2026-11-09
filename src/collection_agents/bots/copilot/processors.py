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


class AICopilotVADProcessor(FrameProcessor):
    """Runs VAD after the copilot protocol router has materialized audio frames."""

    def __init__(self, vad_analyzer, **kwargs):
        super().__init__(**kwargs)
        self._vad_analyzer = vad_analyzer
        self._vad_state = VADState.QUIET

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            sample_rate = getattr(frame, "audio_in_sample_rate", None) or 8000
            self._vad_analyzer.set_sample_rate(sample_rate)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            if not self._vad_analyzer.sample_rate:
                self._vad_analyzer.set_sample_rate(frame.sample_rate or 8000)

            new_vad_state = await self._vad_analyzer.analyze_audio(frame.audio)
            if (
                new_vad_state != self._vad_state
                and new_vad_state not in (VADState.STARTING, VADState.STOPPING)
            ):
                if new_vad_state == VADState.SPEAKING:
                    await self.push_frame(
                        VADUserStartedSpeakingFrame(
                            start_secs=self._vad_analyzer.params.start_secs,
                        ),
                        direction,
                    )
                elif new_vad_state == VADState.QUIET:
                    await self.push_frame(
                        VADUserStoppedSpeakingFrame(
                            stop_secs=self._vad_analyzer.params.stop_secs,
                        ),
                        direction,
                    )
                self._vad_state = new_vad_state

            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


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
