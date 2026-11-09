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

from collection_agents.processors.common import (
    HangupState,
)
from pipecat.utils.string import TextPartForConcatenation, concatenate_aggregated_text

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


class ReplyProtectionInterruptionGate(FrameProcessor):
    """Allows one interruption, then protects the next bot reply from further interruptions."""

    def __init__(self, interruption_state: dict | None = None):
        super().__init__()
        self._awaiting_protected_reply = False
        self._protecting_reply = False
        self._bot_speaking = False
        self._bot_generating = False
        self._interruption_state = interruption_state if isinstance(interruption_state, dict) else {}
        self._interruption_state.setdefault("assistant_turn_interrupted", False)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._bot_generating = True
            self._interruption_state["assistant_turn_interrupted"] = False
            if self._awaiting_protected_reply:
                logging.info("[ReplyProtectionInterruptionGate] Arming protected bot reply after interruption")
                self._protecting_reply = True

        elif isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            if self._awaiting_protected_reply and not self._protecting_reply:
                logging.info("[ReplyProtectionInterruptionGate] Protecting bot reply on speech start after interruption")
                self._protecting_reply = True

        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._protecting_reply:
                logging.info("[ReplyProtectionInterruptionGate] Releasing protection after bot reply completed")
                self._protecting_reply = False
                self._awaiting_protected_reply = False

        elif isinstance(frame, LLMFullResponseEndFrame):
            self._bot_generating = False
            if self._protecting_reply and not self._bot_speaking:
                logging.info("[ReplyProtectionInterruptionGate] Releasing protection after non-spoken reply completed")
                self._protecting_reply = False
                self._awaiting_protected_reply = False

        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._awaiting_protected_reply = False
            self._protecting_reply = False
            self._bot_speaking = False
            self._bot_generating = False
            self._interruption_state["assistant_turn_interrupted"] = False

        if isinstance(frame, InterruptionFrame):
            if self._protecting_reply:
                logging.info("[ReplyProtectionInterruptionGate] Suppressing interruption during protected bot reply")
                return
            self._awaiting_protected_reply = True
            self._interruption_state["assistant_turn_interrupted"] = True
            logging.info("[ReplyProtectionInterruptionGate] Allowing interruption and protecting next bot reply")

        await self.push_frame(frame, direction)


class STTGatedInterruptor(FrameProcessor):
    """只有 STT 确认有效转录文本后，才放行 UserStartedSpeakingFrame/UserStoppedSpeakingFrame。

    防止线路噪音被 VAD 误判为用户说话，从而不必要地打断 bot。
    若超时内 STT 无有效输出，则丢弃暂存的帧，bot 继续说话不受打断。
    同时在一次有效打断后增加冷却时间，避免短时间内连续多次打断。

    使用 InterimTranscriptionFrame（中间结果）做快速确认：只要中间结果达到
    min_interim_chars 个字符，即认为是真实语音并立即放行，无需等最终结果。
    这样打断响应更快，也避免了因 endpointing 延迟导致的超时误判。
    """

    def __init__(
        self,
        confirmation_timeout: float = 3.0,
        interruption_cooldown_sec: float = 2.0,
        min_interim_chars: int = 2,
    ):
        super().__init__()
        self._timeout = confirmation_timeout
        self._interruption_cooldown_sec = interruption_cooldown_sec
        self._min_interim_chars = min_interim_chars
        self._pending_start: tuple | None = None   # (frame, direction)
        self._pending_stop: tuple | None = None    # (frame, direction)
        self._waiting = False
        self._timeout_task = None
        self._last_released_at = 0.0
        self._suppress_current_turn = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            now = time.monotonic()
            if self._last_released_at and (now - self._last_released_at) < self._interruption_cooldown_sec:
                logging.info(
                    "[STTGatedInterruptor] Suppressing repeated interruption within cooldown: elapsed=%.3fs cooldown=%.3fs",
                    now - self._last_released_at,
                    self._interruption_cooldown_sec,
                )
                self._suppress_current_turn = True
                return
            self._pending_start = (frame, direction)
            self._pending_stop = None
            self._waiting = True
            self._reset_timeout()
            return  # hold, don't pass downstream yet

        if isinstance(frame, UserStoppedSpeakingFrame):
            if self._suppress_current_turn:
                self._suppress_current_turn = False
                return
            if self._waiting:
                self._pending_stop = (frame, direction)
                return  # hold until STT confirms

        # Interim result: fast-path confirmation — release immediately if enough chars
        if type(frame).__name__ == "InterimTranscriptionFrame":
            if self._waiting and len(frame.text.strip()) >= self._min_interim_chars:
                logging.info(
                    "[STTGatedInterruptor] Interim result confirms real speech (%d chars), releasing held frames",
                    len(frame.text.strip()),
                )
                self._cancel_timeout()
                self._waiting = False
                await self._release_pending()
            elif self._suppress_current_turn:
                self._suppress_current_turn = False
                return
            await self.push_frame(frame, direction)
            return

        # Final transcription: fallback confirmation if interim never triggered
        if isinstance(frame, TranscriptionFrame):
            if self._waiting:
                self._cancel_timeout()
                self._waiting = False
                if frame.text.strip():
                    await self._release_pending()
                else:
                    # Empty final result (noise): discard held frames
                    logging.debug("[STTGatedInterruptor] Empty transcription, discarding held speaking frames")
                    self._discard_pending()
            elif self._suppress_current_turn:
                self._suppress_current_turn = False
                logging.info("[STTGatedInterruptor] Suppressing TranscriptionFrame for suppressed turn")
                return
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            self._cancel_timeout()
            self._discard_pending()
            self._waiting = False
            self._suppress_current_turn = False
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


    async def _release_pending(self):
        released = False
        if self._pending_start:
            await self.push_frame(*self._pending_start)
            self._pending_start = None
            released = True
        if self._pending_stop:
            await self.push_frame(*self._pending_stop)
            self._pending_stop = None
        if released:
            self._last_released_at = time.monotonic()

    def _discard_pending(self):
        self._pending_start = None
        self._pending_stop = None

    def _reset_timeout(self):
        self._cancel_timeout()
        self._timeout_task = self.create_task(self._timeout_handler(), "stt_gate_timeout")

    def _cancel_timeout(self):
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        self._timeout_task = None

    async def _timeout_handler(self):
        try:
            await asyncio.sleep(self._timeout)
            if self._waiting:
                logging.info("[STTGatedInterruptor] Timeout: no valid STT, discarding held frames")
                self._discard_pending()
                self._waiting = False
                self._suppress_current_turn = True  # suppress any late-arriving TranscriptionFrame
        except asyncio.CancelledError:
            pass


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


class ContextSafeAssistantTurnGate(FrameProcessor):
    """Only commits uninterrupted assistant turns to downstream context."""

    def __init__(self, interruption_state: dict | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._in_assistant_turn = False
        self._buffered_text: list[TextPartForConcatenation] = []
        self._interruption_state = interruption_state if isinstance(interruption_state, dict) else {}
        self._interruption_state.setdefault("assistant_turn_interrupted", False)
        self._pending_commit_text: str | None = None  # buffered until BotStoppedSpeakingFrame

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            if self._pending_commit_text:
                # New LLM turn started while previous TTS was still pending commit.
                # This only happens when the old TTS was cut short by an interruption
                # (otherwise BotStoppedSpeakingFrame would have already cleared it).
                logging.info(
                    "[ContextSafeAssistantTurnGate] New LLM turn started with stale pending commit — dropping: %r",
                    self._pending_commit_text[:200],
                )
                self._pending_commit_text = None
            self._in_assistant_turn = True
            self._interruption_state["assistant_turn_interrupted"] = False
            self._buffered_text = []
            logging.info(
                "[ContextSafeAssistantTurnGate] Assistant turn started direction=%s",
                direction,
            )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            # Per-chunk TextFrame logs are too noisy for production INFO logs.
            # Uncomment when debugging assistant context buffering.
            # logging.debug(
            #     "[ContextSafeAssistantTurnGate] Saw TextFrame direction=%s in_turn=%s append_to_context=%s text=%r",
            #     direction,
            #     self._in_assistant_turn,
            #     getattr(frame, "append_to_context", None),
            #     frame.text[:120],
            # )
            if self._in_assistant_turn and frame.append_to_context:
                self._buffered_text.append(
                    TextPartForConcatenation(
                        frame.text,
                        includes_inter_part_spaces=frame.includes_inter_frame_spaces,
                    )
                )
                frame.append_to_context = False
                # Per-chunk TextFrame logs are too noisy for production INFO logs.
                # Uncomment when debugging assistant context buffering.
                # logging.debug(
                #     "[ContextSafeAssistantTurnGate] Buffered assistant text chunk and suppressed immediate context append: %r",
                #     frame.text[:120],
                # )
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            interrupted = bool(self._interruption_state.get("assistant_turn_interrupted"))
            logging.info(
                "[ContextSafeAssistantTurnGate] Assistant turn ending direction=%s interrupted=%s buffered_chunks=%d",
                direction,
                interrupted,
                len(self._buffered_text),
            )
            if self._in_assistant_turn and not interrupted and self._buffered_text:
                aggregated = concatenate_aggregated_text(self._buffered_text)
                if aggregated:
                    # Don't commit yet — TTS may still be playing. Hold until BotStoppedSpeakingFrame
                    # to detect whether the user interrupted mid-speech.
                    self._pending_commit_text = aggregated
                    logging.info(
                        "[ContextSafeAssistantTurnGate] Holding assistant turn pending TTS completion: %r",
                        aggregated[:200],
                    )
            elif self._in_assistant_turn and interrupted:
                dropped = concatenate_aggregated_text(self._buffered_text).strip()
                logging.info(
                    "[ContextSafeAssistantTurnGate] Dropping interrupted assistant turn from context: %r",
                    dropped[:200],
                )
            self._in_assistant_turn = False
            self._interruption_state["assistant_turn_interrupted"] = False
            self._buffered_text = []
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            if self._pending_commit_text:
                interrupted = bool(self._interruption_state.get("assistant_turn_interrupted"))
                if not interrupted:
                    logging.info(
                        "[ContextSafeAssistantTurnGate] TTS completed normally, committing assistant turn: %r",
                        self._pending_commit_text[:200],
                    )
                    text_frame = TextFrame(self._pending_commit_text)
                    text_frame.includes_inter_frame_spaces = True
                    await self.push_frame(text_frame, FrameDirection.DOWNSTREAM)
                    await self.push_frame(LLMAssistantPushAggregationFrame(), FrameDirection.DOWNSTREAM)
                else:
                    logging.info(
                        "[ContextSafeAssistantTurnGate] TTS was interrupted, dropping assistant turn: %r",
                        self._pending_commit_text[:200],
                    )
                self._pending_commit_text = None
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (EndFrame, CancelFrame)):
            if self._pending_commit_text:
                logging.info(
                    "[ContextSafeAssistantTurnGate] Dropping pending commit on %s",
                    type(frame).__name__,
                )
                self._pending_commit_text = None
            if self._in_assistant_turn and self._buffered_text:
                logging.info(
                    "[ContextSafeAssistantTurnGate] Resetting assistant turn state on %s; interrupted=%s buffered_chunks=%d",
                    type(frame).__name__,
                    bool(self._interruption_state.get("assistant_turn_interrupted")),
                    len(self._buffered_text),
                )
            self._in_assistant_turn = False
            self._interruption_state["assistant_turn_interrupted"] = False
            self._buffered_text = []
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


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
