import asyncio
import logging
import time

_logger = logging.getLogger(__name__)

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


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

class STTConfirmedInterruptionGate(FrameProcessor):
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
                    "[STTConfirmedInterruptionGate] Suppressing repeated interruption within cooldown: elapsed=%.3fs cooldown=%.3fs",
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
                    "[STTConfirmedInterruptionGate] Interim result confirms real speech (%d chars), releasing held frames",
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
                    logging.debug("[STTConfirmedInterruptionGate] Empty transcription, discarding held speaking frames")
                    self._discard_pending()
            elif self._suppress_current_turn:
                self._suppress_current_turn = False
                logging.info("[STTConfirmedInterruptionGate] Suppressing TranscriptionFrame for suppressed turn")
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
                logging.info("[STTConfirmedInterruptionGate] Timeout: no valid STT, discarding held frames")
                self._discard_pending()
                self._waiting = False
                self._suppress_current_turn = True  # suppress any late-arriving TranscriptionFrame
        except asyncio.CancelledError:
            pass
