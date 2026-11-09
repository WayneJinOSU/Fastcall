import asyncio
import logging
from collections import deque

from pipecat.frames.frames import Frame, TextFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.events import VoicemailDetectedEvent, VoicemailDetectedFrame
from collection_agents.processors.common.voicemail_patterns import VOICEMAIL_REGEX


class VoicemailDetector(FrameProcessor):
    """Detects voicemail evidence without choosing call-flow actions."""

    def __init__(
        self,
        max_word_count=30,
        mode="standard",
        on_detected=None,
    ):
        super().__init__()
        if mode not in {"standard", "realtime"}:
            raise ValueError(f"Unsupported voicemail detector mode: {mode}")
        self._mode = mode
        self._realtime = mode == "realtime"
        self._on_detected = on_detected
        self._detected = False
        self._word_buffer = deque(maxlen=max_word_count)

        self._voicemail_regex = VOICEMAIL_REGEX

    async def _emit_detection(self, event, direction):
        if self._on_detected:
            result = self._on_detected(event)
            if asyncio.iscoroutine(result):
                await result
            return
        await self.push_frame(
            VoicemailDetectedFrame(event=event),
            direction,
        )

    @staticmethod
    def _item_id(frame):
        result = getattr(frame, "result", None)
        return str(getattr(result, "item_id", "") or "")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        is_transcription = (
            isinstance(frame, TranscriptionFrame)
            if self._realtime
            else isinstance(frame, (TextFrame, TranscriptionFrame))
            or type(frame).__name__ == "InterimTranscriptionFrame"
        )
        if (
            is_transcription
            and not self._detected
            and (not self._realtime or direction == FrameDirection.UPSTREAM)
        ):
            text = frame.text.strip()
            if text:
                self._word_buffer.extend(text.split())
                check_text = " ".join(self._word_buffer)
                match = self._voicemail_regex.search(check_text.lower())
                if match:
                    logging.info(
                        "[VoicemailDetector] Detected voicemail. mode=%s "
                        "matched_text=%r",
                        self._mode,
                        check_text,
                    )
                    self._detected = True
                    self._word_buffer.clear()
                    event = VoicemailDetectedEvent(
                        transcript_text=check_text,
                        trigger_text=text,
                        matched_text=match.group(0),
                        source=self._mode,
                        item_id=self._item_id(frame),
                    )
                    await self._emit_detection(event, direction)
                    return

        await self.push_frame(frame, direction)
