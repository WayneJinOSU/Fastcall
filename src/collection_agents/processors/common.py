import asyncio
import logging
import time

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    StartFrame,
    TranscriptionFrame,
)
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


class SilenceDetector(FrameProcessor):
    """Detects no-response windows and emits `on_silence_detected`."""

    def __init__(
        self,
        timeout_sec=6,
        cooldown_sec=None,
        check_interval_sec=0.5,
        component="common",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._component = str(component or "common")
        self._timeout_sec = timeout_sec
        self._cooldown_sec = cooldown_sec if cooldown_sec is not None else timeout_sec
        self._check_interval_sec = check_interval_sec
        now = time.monotonic()
        self._last_user_activity = now
        self._last_bot_activity = now
        self._last_emit_time = now
        self._bot_speaking = False
        self._bot_generating = False
        self._has_bot_started_speaking_once = False
        self._silence_count = 0
        self._monitor_task = None
        self._check_lock = asyncio.Lock()
        self._register_event_handler("on_silence_detected")

    def _mark_user_activity(self, now: float):
        self._last_user_activity = now

    def mark_user_activity(self, now: float = None):
        self._mark_user_activity(time.monotonic() if now is None else now)

    async def _maybe_emit_silence(self, now: float):
        async with self._check_lock:
            # Avoid false no-response before first bot utterance is actually played.
            if not self._has_bot_started_speaking_once:
                return

            user_silent = now - self._last_user_activity > self._timeout_sec
            bot_idle = (
                (not self._bot_speaking)
                and (not self._bot_generating)
                and (now - self._last_bot_activity > self._timeout_sec)
            )
            cooldown_ok = now - self._last_emit_time > self._cooldown_sec

            if user_silent and bot_idle and cooldown_ok:
                logging.info(
                    "[SilenceDetector:%s] Detected silence window: "
                    f"user_silent={now - self._last_user_activity:.2f}s, "
                    f"bot_idle={now - self._last_bot_activity:.2f}s, "
                    f"count={self._silence_count}",
                    self._component,
                )
                await self._call_event_handler(
                    "on_silence_detected",
                    silence_count=self._silence_count,
                )
                self._silence_count += 1
                self._last_emit_time = now
                # Reset anchors so we do not retrigger immediately on the next check.
                self._last_user_activity = now
                self._last_bot_activity = now

    def _ensure_monitor(self):
        if not self._monitor_task or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _stop_monitor(self):
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

    async def cleanup(self):
        # EndFrame may be pushed mid-pipeline (e.g. by GracefulHangupController)
        # and never reach this processor, leaving the monitor loop running as a
        # zombie. cleanup() is guaranteed to be called on pipeline teardown.
        await super().cleanup()
        await self._stop_monitor()

    async def _monitor_loop(self):
        try:
            while True:
                await asyncio.sleep(self._check_interval_sec)
                await self._maybe_emit_silence(time.monotonic())
        except asyncio.CancelledError:
            return

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        now = time.monotonic()
        frame_name = type(frame).__name__

        if isinstance(frame, StartFrame):
            self._ensure_monitor()

        # User-side activity: either speaking lifecycle or recognized text.
        if isinstance(frame, TranscriptionFrame) or frame_name in {
            "InterimTranscriptionFrame",
            "UserStartedSpeakingFrame",
            "UserStoppedSpeakingFrame",
            "UserSpeakingFrame",
        }:
            self.mark_user_activity(now)

        # Bot-side activity: speaking lifecycle.
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            self._has_bot_started_speaking_once = True
            self._last_bot_activity = now
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._last_bot_activity = now
        elif isinstance(frame, LLMFullResponseStartFrame):
            self._bot_generating = True
            self._last_bot_activity = now
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._bot_generating = False
            self._has_bot_started_speaking_once = True
            self._last_bot_activity = now

        await self._maybe_emit_silence(now)

        if isinstance(frame, (EndFrame, CancelFrame)):
            self._bot_generating = False
            await self._stop_monitor()

        await self.push_frame(frame, direction)


def _normalize_lang_code(language: str, default: str = "es") -> str:
    """把各种语言写法归一到两位小写码（es-MX -> es，fil -> tl）。"""
    if not language:
        return default
    lang_code = language.split("-")[0].strip().lower()
    if not lang_code:
        return default
    if lang_code == "fil":
        return "tl"
    return lang_code


class HangupState:
    """挂机状态（standard / realtime 共用）。

    block_ghost_llm 仅 standard 使用：配置固定语音信箱文本时置位，
    阻断 voicemail 检测后由 LLMUserAggregator 内部定时器触发的幽灵 LLM 调用。
    """

    def __init__(self):
        self.is_hanging_up = False
        self.input_blocked = False
        self.final_phrase_detected = False
        self.block_ghost_llm = False


class GracefulHangupController(FrameProcessor):
    """Hangs up after terminal assistant audio finishes, with a fallback timer."""

    def __init__(
        self,
        *,
        hangup_state: HangupState,
        hangup_cb=None,
        fallback_exit_delay=25.0,
        post_bot_stop_delay=0.2,
        end_frame_delay=0.5,
    ):
        super().__init__()
        self._hangup_state = hangup_state
        self._hangup_cb = hangup_cb
        self._fallback_exit_delay = fallback_exit_delay
        self._post_bot_stop_delay = post_bot_stop_delay
        self._end_frame_delay = end_frame_delay
        self._fallback_task = None
        self._bot_stop_task = None
        self._hangup_command_sent = False
        self._is_ended = False
        self._hangup_direction = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (EndFrame, CancelFrame)):
            self._is_ended = True
            self._cancel_fallback_task()
            self._cancel_bot_stop_task()
            await self.push_frame(frame, direction)
            return

        if self._should_control_hangup() and not self._hangup_command_sent:
            self._hangup_direction = self._hangup_direction or direction
            if isinstance(frame, BotStoppedSpeakingFrame):
                self._cancel_fallback_task()
                self._ensure_bot_stop_task(self._hangup_direction)
            else:
                self._ensure_fallback_task(self._hangup_direction)

        await self.push_frame(frame, direction)

    def _should_control_hangup(self) -> bool:
        return (
            self._hangup_state is not None
            and self._hangup_state.is_hanging_up
            and self._hangup_state.final_phrase_detected
            and not self._is_ended
        )

    def _ensure_fallback_task(self, direction: FrameDirection):
        if self._fallback_task or self._hangup_command_sent or self._is_ended:
            return
        logging.info(
            "[GracefulHangupController] Final phrase detected; "
            "fallback hangup in %.1fs.",
            self._fallback_exit_delay,
        )
        self._fallback_task = self.create_task(
            self._perform_hangup_sequence(
                direction,
                initial_delay=self._fallback_exit_delay,
                reason="fallback",
            ),
            "graceful_hangup_fallback",
        )

    def _ensure_bot_stop_task(self, direction: FrameDirection):
        if self._bot_stop_task or self._hangup_command_sent or self._is_ended:
            return
        logging.info(
            "[GracefulHangupController] Bot stopped after final phrase; "
            "hanging up in %.1fs.",
            self._post_bot_stop_delay,
        )
        self._bot_stop_task = self.create_task(
            self._perform_hangup_sequence(
                direction,
                initial_delay=self._post_bot_stop_delay,
                reason="bot_stopped",
            ),
            "graceful_hangup_after_bot_stopped",
        )

    def _cancel_fallback_task(self):
        if (
            self._fallback_task
            and hasattr(self._fallback_task, "done")
            and not self._fallback_task.done()
        ):
            self._fallback_task.cancel()
        self._fallback_task = None

    def _cancel_bot_stop_task(self):
        if (
            self._bot_stop_task
            and hasattr(self._bot_stop_task, "done")
            and not self._bot_stop_task.done()
        ):
            self._bot_stop_task.cancel()
        self._bot_stop_task = None

    async def _perform_hangup_sequence(
        self,
        direction: FrameDirection,
        *,
        initial_delay: float,
        reason: str,
    ):
        try:
            await asyncio.sleep(initial_delay)
            if self._hangup_command_sent or self._is_ended:
                return

            self._hangup_command_sent = True
            self._hangup_state.is_hanging_up = True
            self._hangup_state.input_blocked = True

            if self._hangup_cb:
                result = self._hangup_cb()
                if asyncio.iscoroutine(result):
                    await result
                logging.info(
                    "[GracefulHangupController] Sent hangup command: reason=%s",
                    reason,
                )

            await asyncio.sleep(self._end_frame_delay)
            if not self._is_ended:
                self._is_ended = True
                logging.info("[GracefulHangupController] pushing EndFrame")
                await self.push_frame(EndFrame(), direction)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.error("[GracefulHangupController] Hangup sequence error: %s", exc)
