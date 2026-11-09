import asyncio
import logging

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


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
