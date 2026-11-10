import logging
import re

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TextFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.processors.common.hangup import HangupState


class HangupPhraseDetector(FrameProcessor):
    def __init__(
        self,
        config_manager,
        transport,
        websocket_client,
        hangup_state: HangupState = None,
        exit_delay=15.0,
        mode="standard",
    ):
        super().__init__()
        if mode not in {"standard", "realtime"}:
            raise ValueError(f"Unsupported word sanitizer mode: {mode}")
        self._realtime = mode == "realtime"
        self._buffer = ""
        self._max_word_count = 200
        self._hangup_state = hangup_state
        self._is_hanging_up = False

        self._endcall_regex = re.compile(
            r"(?is)("
            r"\b(?:goodbye|bye|bye-bye|see you|have a great day|have a good one|"
            r"adi[oó]s|hasta\s+(?:luego|mañana|pronto)|nos\s+vemos|chao|chau|cu[ií]date|"
            r"paalam|hanggang\s+sa\s+muli|ingat\s+ka|"
            r"que\s+tenga\s+(?:un\s+)?(?:lindo|buen|excelente)\s+d[ií]a)\b|"
            r"再见|拜拜|挂了|"
            r"अलविदा|धन्यवाद\s+और\s+अलविदा|नमस्ते\s+फिर\s+मिलेंगे|फिर\s+मिलेंगे|शुभकामनाएं|"
            r"\[\[HANGUP\]\]"
            r")\s*[\]）)]?[\s。.!！?？,，、;；:：\"'”’）)]*$"
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self._is_hanging_up or (
            not self._realtime
            and self._hangup_state is not None
            and self._hangup_state.is_hanging_up
        ):
            if isinstance(frame, (TextFrame, InterruptionFrame)):
                return
            await self.push_frame(frame, direction)
            return

        if self._realtime and isinstance(frame, LLMTextFrame):
            await self.push_frame(frame, direction)
            return

        is_assistant_text = (
            isinstance(frame, TTSTextFrame)
            if self._realtime
            else isinstance(frame, TextFrame)
        )
        if is_assistant_text:
            self._buffer += frame.text
            if len(self._buffer.strip().split()) >= self._max_word_count:
                if self._realtime:
                    self._buffer = self._buffer[-1000:]
                else:
                    await self._sanitize_and_push(self._buffer, direction)
                    self._buffer = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            if self._buffer.strip():
                await self._sanitize_and_push(self._buffer, direction)
                self._buffer = ""
            if isinstance(frame, EndFrame):
                logging.info("[HangupPhraseDetector] Pushing EndFrame: %s", frame)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _sanitize_and_push(self, text: str, direction: FrameDirection):
        clean_text = text.strip()
        if not clean_text:
            return

        logging.debug(
            "[HangupPhraseDetector] checking assistant transcript buffer: %r",
            clean_text[:120],
        )
        if self._endcall_regex.search(clean_text) and not self._is_hanging_up:
            logging.info(
                "[HangupPhraseDetector] Detected tail end phrase; "
                "marking graceful hangup intent."
            )
            self._is_hanging_up = True
            if self._hangup_state is not None:
                self._hangup_state.is_hanging_up = True
                self._hangup_state.final_phrase_detected = True
