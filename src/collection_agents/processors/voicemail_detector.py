import asyncio
import logging
import re
from collections import deque

from pipecat.frames.frames import (
    Frame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.events import (
    VoicemailDetectedEvent,
    VoicemailDetectedFrame,
)


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

        self._voicemail_regex = re.compile(
            r"(?i)("
            r"\b(?:b?uz[oó]n|vicio\s+de\s+buz[oó]n)\b|"
            r"\bcorreo\s+de\s+voz\b|"
            r"\b(?:grab[ae]|deje|dejas|dejar|deja)\s+(?:su|tu|un|el)\s+(?:mensaje|recado|nombre|n[uú]mero|tel[eé]fono)\b|"
            r"\b(?:su|tu)\s+mensaje\b|"
            r"\bmotivo\s+de\s+(?:su|tu)\s+llamada\b|"
            r"\bno\s+(?:puedo|puede)\s+(?:atender|contestar)\b|"
            r"\bno\s+(?:estoy|est[aá])\s+disponible\b|"
            r"\b(?:llam[ae]|vuelva\s+a\s+llamar)\s+m[aá]s\s+tarde\b|"
            r"\b(?:[aá]rea\s+)?fuera\s+de\s+(?:servicio|cobertura|l[ií]nea|[aá]rea)\b|"
            r"\bdespu[eé]s\s+(?:del\s+tono|de\s+la\s+se[ñn]al)\b|"
            r"\b(?:pulsa|presiona|oprime|marca|marque)\s+(?:la\s+)?(?:tecla\s+(?:de\s+)?)?(?:almohadilla|numeral|gato|signo\s+de\s+n[uú]mero|n[uú]mero)\b|"
            r"\bmarquela\b|"
            r"\b(?:(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\s+){4,}(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\b|"
            r"\b(?:espera|espere)\s+instrucciones\b|"
            r"\bcontestador\b|"
            r"\bn[uú]mero\b.*?(?:marcado|marc[oó]|no\s+existe)\b|"
            r"\bcliente\s+n[uú]mero\b|"
            r"\bcobro\s+revertido\b|"
            r"\bvoice\s*mail\b|(?:leave|record)\s+(?:a\s+|your\s+)?message\b|"
            r"\b(leave|record)\s+(?:a\s+|your\s+)?message\b|"
            r"\b(?:press|dial|enter|key\s+in)\s+(?:the\s+)?(?:pound|hash|number|number\s+sign)\b|"
            r"\b(?:wait|follow)\s+(?:for\s+)?instructions\b|"
            r"\b(after|at)\s+the\s+tone\b|"
            r"\bvoice\s*mail\b|"
            r"\bunavailable\b|"
            r"\b(?:pindutin|i-dial|i-enter|ilagay)\s+(?:ang\s+)?(?:pound|hash|numero|number\s+sign)\b|"
            r"\b(?:maghintay|hintayin)\s+(?:ng\s+)?(?:mga\s+)?tagubilin\b|"
            r"\bmag-iwan\s+ng\s+mensahe\b|"
            r"\biwan(?:an)?\s+ng\s+mensahe\b|"
            r"\bpagkatapos\s+ng\s+tunog\b|"
            r"\bhindi\s+(?:available|makontak|maabot)\b|"
            r"\bwala\s+sa\s+serbisyo\b|"
            r"\bh[oộ]p\s+thư\s+tho[aạ]i\b|"
            r"\b(?:vui\s+l[oò]ng\s+)?(?:để|d[eể])\s+l[aạ]i\s+(?:lời|loi)\s+nh[aắ]n\b|"
            r"\bsau\s+(?:tiếng|tieng)\s+(?:b[ií]p|t[ií]n\s+hi[eệ]u|chu[oô]ng)\b|"
            r"\b(?:thu[eê]\s+bao|s[oố]\s+m[aá]y).{0,35}(?:kh[oô]ng\s+li[eê]n\s+l[aạ]c\s+được|t[aạ]m\s+kh[oó]a|kh[oô]ng\s+t[oồ]n\s+t[aạ]i)\b|"
            r"\b(?:kh[oô]ng|chưa)\s+li[eê]n\s+l[aạ]c\s+được\b|"
            r"\bngo[aà]i\s+v[uù]ng\s+ph[uủ]\s+s[oó]ng\b|"
            r"\b(?:xin\s+)?vui\s+l[oò]ng\s+g[oọ]i\s+l[aạ]i\s+sau\b|"
            r"\b(?:b[aấ]m|nh[aấ]n)\s+(?:ph[ií]m|s[oố])\b|"
            r"听到?\s*滴?\s*声|"
            r"开始\s*留言|"
            r"无法\s*接通|"
            r"语音\s*信箱|語音\s*信箱|"
            r"不在\s*服务区"
            r")"
        )

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
