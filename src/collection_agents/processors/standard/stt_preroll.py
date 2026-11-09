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

class STTPreRollMuteProcessor(FrameProcessor):
    """Mutes Deepgram during bot speech to reduce ASR billing cost.

    Maintains a rolling audio buffer so that when a user interrupts mid-speech,
    the buffered pre-VAD audio is replayed to Deepgram before live audio resumes.
    This preserves STTGatedInterruptor's ability to confirm short words (e.g. "hello",
    "si") via InterimTranscriptionFrame without requiring continuous Deepgram streaming.

    Audio gating is done entirely at this processor layer (no STTMuteFrame), allowing
    a keepalive task to inject brief silence frames every _KEEPALIVE_INTERVAL seconds
    so Deepgram's ASR model stays warm during long bot-speech periods.

    Billing impact: Deepgram only charges for audio actually sent. Muting during bot
    speech (typically 50%+ of call duration) halves the effective ASR cost; keepalive
    silence adds <2% overhead.
    """

    _PREROLL_MS = 500
    # Seconds between keepalive silence injections while muted.
    # Must be < Deepgram's ASR model cold-start threshold (~10s).
    _KEEPALIVE_INTERVAL = 8.0
    # Duration of each keepalive silence frame in milliseconds.
    _KEEPALIVE_SILENCE_MS = 100

    def __init__(self, sample_rate: int = 8000, **kwargs):
        super().__init__(**kwargs)
        self._sample_rate = sample_rate
        self._muted = False
        # Buffer stores (frame, duration_secs) tuples.
        # We trim by total duration instead of frame count so large telephony frames
        # (e.g. 1s per packet) don't inflate replay volume beyond _PREROLL_MS.
        self._buffer: deque = deque()   # elements: (InputAudioRawFrame, float)
        self._buffer_duration: float = 0.0
        self._muted_secs: float = 0.0
        self._keepalive_task: asyncio.Task | None = None

    def _buffer_append(self, frame: InputAudioRawFrame) -> None:
        """Append frame and evict oldest entries to keep buffer within _PREROLL_MS."""
        sr = frame.sample_rate or self._sample_rate
        dur = frame.num_frames / sr if sr > 0 else 0.0
        self._buffer.append((frame, dur))
        self._buffer_duration += dur
        limit = self._PREROLL_MS / 1000.0
        while self._buffer and self._buffer_duration - self._buffer[0][1] >= limit:
            _, old_dur = self._buffer.popleft()
            self._buffer_duration -= old_dur

    def _drain_buffer(self) -> list:
        """Return buffered frames as a list and clear the buffer."""
        frames = [f for f, _ in self._buffer]
        self._buffer.clear()
        self._buffer_duration = 0.0
        return frames

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            self._buffer_append(frame)
            if not self._muted:
                await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStartedSpeakingFrame):
            if not self._muted:
                self._muted = True
                self._mute_start = asyncio.get_event_loop().time()
                self._keepalive_task = asyncio.ensure_future(self._keepalive_loop())
                _logger.debug("[STTPreRollMute] muted (bot started speaking)")
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            if self._muted:
                elapsed = asyncio.get_event_loop().time() - getattr(self, "_mute_start", 0)
                self._muted_secs += elapsed
                self._muted = False
                self._cancel_keepalive()
                # Always replay pre-roll buffer on unmute so Deepgram has audio context
                # for speech that occurred just before bot stopped (covers the interruption
                # race where BotStoppedSpeakingFrame arrives before UserStartedSpeakingFrame).
                preroll_dur = self._buffer_duration
                for buffered_frame in self._drain_buffer():
                    await self.push_frame(buffered_frame, FrameDirection.DOWNSTREAM)
                _logger.debug(
                    "[STTPreRollMute] unmuted (bot stopped speaking), saved %.2fs, replayed %.3fs pre-roll",
                    elapsed, preroll_dur,
                )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            if self._muted:
                # User interrupted while bot was speaking: replay pre-roll buffer
                # so Deepgram receives the full word including its beginning.
                elapsed = asyncio.get_event_loop().time() - getattr(self, "_mute_start", 0)
                self._muted_secs += elapsed
                self._muted = False
                self._cancel_keepalive()
                preroll_dur = self._buffer_duration
                for buffered_frame in self._drain_buffer():
                    await self.push_frame(buffered_frame, FrameDirection.DOWNSTREAM)
                _logger.debug(
                    "[STTPreRollMute] unmuted on interruption, replayed %.3fs pre-roll, saved %.2fs",
                    preroll_dur, elapsed,
                )
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _keepalive_loop(self):
        """Send periodic silence to Deepgram while muted to prevent ASR model cold-start."""
        try:
            while True:
                await asyncio.sleep(self._KEEPALIVE_INTERVAL)
                if not self._muted:
                    break
                silence_samples = int(self._sample_rate * self._KEEPALIVE_SILENCE_MS / 1000)
                silence_frame = InputAudioRawFrame(
                    audio=bytes(silence_samples * 2),  # 16-bit PCM zeros
                    sample_rate=self._sample_rate,
                    num_channels=1,
                )
                await self.push_frame(silence_frame, FrameDirection.DOWNSTREAM)
                _logger.debug("[STTPreRollMute] sent keepalive silence (%dms)", self._KEEPALIVE_SILENCE_MS)
        except asyncio.CancelledError:
            pass

    def _cancel_keepalive(self):
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None

    async def cleanup(self):
        # If the pipeline tears down while muted (mid-pipeline EndFrame never
        # reaches us), the keepalive loop would keep pushing silence frames.
        await super().cleanup()
        self._cancel_keepalive()

    @property
    def muted_secs(self) -> float:
        return self._muted_secs
