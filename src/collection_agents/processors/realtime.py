import asyncio
import logging
import re
from collections import deque

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMRunFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    UserSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from collection_agents.processors.common import (
    GracefulHangupController,
    HangupState,
)


def _compile_any(patterns):
    return re.compile(r"(?is)(" + "|".join(patterns) + r")")


_VOICE_ASSISTANT_PATTERNS = [
    r"\bvo(?:ice|cie|ce)\s+a(?:ssistant|ssitant|sistant|ssistent)\b",
    r"\b(?:virtual|automated|digital|ai)\s+assistant\b",
    r"\bautomated\s+(?:system|service|agent)\b",
    r"\b(?:asistente|assistente)\s+(?:virtual|autom[aá]tico|digital)\b",
    r"\b(?:i am|i'm|this is|soy|somos)\s+(?:an?\s+)?(?:automated|virtual|ai)?\s*assistant\b",
    r"\b(?:how can i help|en qu[eé] puedo ayudar)\b",
    r"语音助手|語音助手|智能助手|智慧助手|我是.*助手|請說出|請按|请说出|请按",
]

_VOICEMAIL_EN_PATTERNS = [
    r"\bvoice\s*mail\b",
    r"\b(?:leave|record)\s+(?:a\s+|your\s+)?message\b",
    r"\b(?:after|at)\s+the\s+tone\b",
    r"\bunavailable\b",
    r"\b(?:wait|follow)\s+(?:for\s+)?instructions\b",
    r"\b(?:press|dial|enter|key\s+in)\s+(?:the\s+)?(?:pound|hash|number|number\s+sign)\b",
]

_VOICEMAIL_ES_PATTERNS = [
    r"\b(?:b?uz[oó]n|vicio\s+de\s+buz[oó]n)\b",
    r"\bcorreo\s+de\s+voz\b",
    r"\bcontestador\b",
    r"\b(?:grab[ae]|deje|dejas|dejar|deja)\s+(?:su|tu|un|el)\s+(?:mensaje|recado|nombre|n[uú]mero|tel[eé]fono)\b",
    r"\b(?:su|tu)\s+mensaje\b",
    r"\bmotivo\s+de\s+(?:su|tu)\s+llamada\b",
    r"\bno\s+(?:puedo|puede)\s+(?:atender|contestar)\b",
    r"\bno\s+(?:estoy|est[aá])\s+disponible\b",
    r"\b(?:llam[ae]|vuelva\s+a\s+llamar)\s+m[aá]s\s+tarde\b",
    r"\b(?:[aá]rea\s+)?fuera\s+de\s+(?:servicio|cobertura|l[ií]nea|[aá]rea)\b",
    r"\bdespu[eé]s\s+(?:del\s+tono|de\s+la\s+se[ñn]al)\b",
    r"\b(?:pulsa|presiona|oprime|marca|marque)\s+(?:la\s+)?(?:tecla\s+(?:de\s+)?)?(?:almohadilla|numeral|gato|signo\s+de\s+n[uú]mero|n[uú]mero)\b",
    r"\bmarquela\b",
    r"\b(?:(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\s+){4,}(?:cero|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\b",
    r"\b(?:espera|espere)\s+instrucciones\b",
    r"\bn[uú]mero\b.*?(?:marcado|marc[oó]|no\s+existe)\b",
    r"\bcliente\s+n[uú]mero\b",
    r"\bcobro\s+revertido\b",
]

_VOICEMAIL_TL_PATTERNS = [
    r"\b(?:pindutin|i-dial|i-enter|ilagay)\s+(?:ang\s+)?(?:pound|hash|numero|number\s+sign)\b",
    r"\b(?:maghintay|hintayin)\s+(?:ng\s+)?(?:mga\s+)?tagubilin\b",
    r"\bmag-iwan\s+ng\s+mensahe\b",
    r"\biwan(?:an)?\s+ng\s+mensahe\b",
    r"\bpagkatapos\s+ng\s+tunog\b",
    r"\bhindi\s+(?:available|makontak|maabot)\b",
    r"\bwala\s+sa\s+serbisyo\b",
]

_VOICEMAIL_ZH_PATTERNS = [
    r"听到?\s*滴?\s*声",
    r"开始\s*留言",
    r"无法\s*接通",
    r"语音\s*信箱|語音\s*信箱",
    r"不在\s*服务区",
]

_PROMPT_INJECTION_PATTERNS = [
    r"ignore (?:all )?(?:previous|prior) instructions",
    r"system prompt",
    r"developer message",
    r"repeat after me",
    r"忽略.*(?:指令|规则|提示)",
    r"不要遵守",
    r"系统提示",
    r"开发者消息",
    r"你现在是",
]


REALTIME_VOICE_ASSISTANT_REGEX = _compile_any(_VOICE_ASSISTANT_PATTERNS)
REALTIME_VOICEMAIL_REGEX = _compile_any(
    _VOICEMAIL_EN_PATTERNS
    + _VOICEMAIL_ES_PATTERNS
    + _VOICEMAIL_TL_PATTERNS
    + _VOICEMAIL_ZH_PATTERNS
)
REALTIME_PROMPT_INJECTION_REGEX = _compile_any(_PROMPT_INJECTION_PATTERNS)


class RealtimeHangupGate(FrameProcessor):
    """Stops late user input from reaching Azure Realtime after hangup starts."""

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
                InputAudioRawFrame,
                InterruptionFrame,
                LLMRunFrame,
                TextFrame,
                TranscriptionFrame,
                UserStartedSpeakingFrame,
                UserStoppedSpeakingFrame,
                UserSpeakingFrame,
            ),
        ) or type(frame).__name__ == "InterimTranscriptionFrame":
            # logging.debug(
            #     "[RealtimeHangupGate] Dropping %s after hangup direction=%s",
            #     type(frame).__name__,
            #     direction,
            # )
            return

        await self.push_frame(frame, direction)


class RealtimeBotSpeakingStateTracker(FrameProcessor):
    """Tracks when assistant audio is being played to the phone line."""

    def __init__(self, monitor_state: dict, on_bot_stopped_speaking_cb=None):
        super().__init__()
        self._state = monitor_state
        self._on_bot_stopped_speaking_cb = on_bot_stopped_speaking_cb
        self._state.setdefault("bot_speaking", False)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        should_notify_stopped = False
        if isinstance(frame, (TTSStartedFrame, BotStartedSpeakingFrame)):
            self._state["bot_speaking"] = True
        elif isinstance(frame, (BotStoppedSpeakingFrame, InterruptionFrame)):
            self._state["bot_speaking"] = False
            should_notify_stopped = True
        elif isinstance(frame, (EndFrame, CancelFrame)):
            self._state["bot_speaking"] = False

        await self.push_frame(frame, direction)
        if should_notify_stopped:
            await self._call_bot_stopped_speaking_callback()

    async def _call_bot_stopped_speaking_callback(self):
        if not self._on_bot_stopped_speaking_cb:
            return
        try:
            result = self._on_bot_stopped_speaking_cb()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logging.warning(
                "[RealtimeBotSpeakingStateTracker] Bot stopped callback failed: %s",
                exc,
            )


class RealtimeInputTranscriptionMonitor(FrameProcessor):
    """Monitors input ASR text without letting unsafe text enter LLM context."""

    def __init__(
        self,
        monitor_state: dict,
        delete_item_cb=None,
        voice_assistant_cb=None,
        partial_barge_in_cb=None,
        confirmed_barge_in_cb=None,
        max_word_count=256,
        min_barge_in_chars=2,
    ):
        super().__init__()
        self._state = monitor_state
        self._delete_item_cb = delete_item_cb
        self._voice_assistant_cb = voice_assistant_cb
        self._partial_barge_in_cb = partial_barge_in_cb
        self._confirmed_barge_in_cb = confirmed_barge_in_cb
        self._word_buffer = deque(maxlen=max_word_count)
        self._item_transcripts = {}
        self._min_barge_in_chars = min_barge_in_chars
        self._state.setdefault("bot_speaking", False)
        self._state.setdefault("assistant_response_active", False)
        self._state.setdefault("barge_in_item_ids", set())
        self._state.setdefault("input_transcription_events", [])
        self._state.setdefault("voice_assistant_triggered", False)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        is_transcription = isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame))
        if direction != FrameDirection.UPSTREAM or not is_transcription:
            await self.push_frame(frame, direction)
            return

        text = (frame.text or "").strip()
        is_final = isinstance(frame, TranscriptionFrame)
        item_id = self._extract_item_id(frame)
        item_transcript = self._buffered_item_text(item_id, text)
        barge_in_ids = self._state.setdefault("barge_in_item_ids", set())
        is_barge_in = (
            self._state.get("bot_speaking", False)
            or self._state.get("assistant_response_active", False)
            or (item_id and item_id in barge_in_ids)
        )
        check_text = self._buffered_text(text)
        classification, classification_detail = self.classify_with_detail(check_text)
        should_suppress = is_barge_in or classification in {
            "voice_assistant",
            "prompt_injection",
        }

        if (
            is_barge_in
            and classification == "barge_in"
            and self._is_effective_barge_in(text)
        ):
            action = "confirmed_barge_in" if is_final else "partial_barge_in"
            event = {
                "item_id": item_id,
                "text": text,
                "transcript_text": self._event_transcript_text(
                    text,
                    item_transcript,
                    check_text,
                    classification,
                ),
                "check_text": check_text,
                "classification": classification,
                "classification_detail": classification_detail,
                "is_barge_in": True,
                "is_final": is_final,
                "action": action,
            }
            if self._should_record_barge_in_event(event):
                self._state.setdefault("input_transcription_events", []).append(event)
            if item_id:
                if is_final:
                    barge_in_ids.discard(item_id)
                    self._forget_item_text(item_id)
                else:
                    barge_in_ids.add(item_id)
            handled = await self._call_barge_in_callback(event)
            if handled:
                return
            await self.push_frame(frame, direction)
            return

        if not should_suppress:
            if is_final:
                self._forget_item_text(item_id)
            await self.push_frame(frame, direction)
            return

        event = {
            "item_id": item_id,
            "text": text,
            "transcript_text": self._event_transcript_text(
                text,
                item_transcript,
                check_text,
                classification,
            ),
            "check_text": check_text,
            "classification": classification,
            "classification_detail": classification_detail,
            "is_barge_in": bool(is_barge_in),
            "is_final": is_final,
            "action": "suppressed",
        }
        self._state.setdefault("input_transcription_events", []).append(event)
        if classification == "voice_assistant":
            self._state["peer_assistant_likely"] = True
        elif classification == "prompt_injection":
            self._state["voice_injection_likely"] = True

        logging.info(
            "[RealtimeInputTranscriptionMonitor] Suppressed input transcript: "
            "class=%s detail=%s is_barge_in=%s item_id=%s text=%r "
            "transcript=%r check_text=%r",
            classification,
            classification_detail,
            bool(is_barge_in),
            item_id,
            text[:180],
            event.get("transcript_text", "")[:180],
            check_text[:180],
        )

        if item_id:
            barge_in_ids.discard(item_id)
            self._forget_item_text(item_id)
            await self._delete_remote_item(item_id)

        if (
            classification == "voice_assistant"
            and not self._state.get("voice_assistant_triggered")
        ):
            self._state["voice_assistant_triggered"] = True
            await self._call_voice_assistant_callback(event)

    def _buffered_text(self, text: str) -> str:
        if text:
            self._word_buffer.extend(text.split())
        return " ".join(self._word_buffer)

    def _buffered_item_text(self, item_id: str, text: str) -> str:
        if not item_id:
            return text
        if not text:
            return self._item_transcripts.get(item_id, "")
        transcript = self._join_incremental_text(
            self._item_transcripts.get(item_id, ""),
            text,
        )
        self._item_transcripts[item_id] = transcript
        if len(self._item_transcripts) > 128:
            oldest_item_id = next(iter(self._item_transcripts))
            self._item_transcripts.pop(oldest_item_id, None)
        return transcript

    def _forget_item_text(self, item_id: str):
        if item_id:
            self._item_transcripts.pop(item_id, None)

    @staticmethod
    def _join_incremental_text(previous: str, text: str) -> str:
        previous = (previous or "").strip()
        text = (text or "").strip()
        if not previous:
            return text
        if not text:
            return previous
        if text == previous or previous.endswith(text):
            return previous
        if text.startswith(previous):
            return text
        return f"{previous} {text}".strip()

    @staticmethod
    def _event_transcript_text(
        text: str,
        item_transcript: str,
        check_text: str,
        classification: str,
    ) -> str:
        if classification == "voice_assistant":
            return (item_transcript or check_text or text or "").strip()
        return (text or "").strip()

    @staticmethod
    def _extract_item_id(frame) -> str:
        result = getattr(frame, "result", None)
        return str(getattr(result, "item_id", "") or "")

    def _should_record_barge_in_event(self, event: dict) -> bool:
        if event.get("is_final"):
            return True
        text = event.get("text", "")
        item_id = event.get("item_id", "")
        last = self._state.get("last_partial_barge_in_event") or {}
        if last.get("item_id") == item_id and last.get("text") == text:
            return False
        self._state["last_partial_barge_in_event"] = {
            "item_id": item_id,
            "text": text,
        }
        return True

    def _is_effective_barge_in(self, text: str) -> bool:
        compact = re.sub(r"[\W_]+", "", text or "", flags=re.UNICODE).lower()
        if not compact:
            return False
        if compact in {"um", "uh", "er", "ah", "hmm", "嗯", "呃", "啊", "喂"}:
            return False
        return len(compact) >= self._min_barge_in_chars

    @classmethod
    def classify(cls, text: str) -> str:
        classification, _ = cls.classify_with_detail(text)
        return classification

    @classmethod
    def classify_with_detail(cls, text: str) -> tuple[str, str]:
        if REALTIME_PROMPT_INJECTION_REGEX.search(text):
            return "prompt_injection", "prompt_injection"
        if REALTIME_VOICEMAIL_REGEX.search(text):
            return "voice_assistant", "voicemail"
        if REALTIME_VOICE_ASSISTANT_REGEX.search(text):
            return "voice_assistant", "assistant"
        return "barge_in", "barge_in"

    async def _delete_remote_item(self, item_id: str):
        if not self._delete_item_cb:
            return
        try:
            result = self._delete_item_cb(item_id)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logging.warning(
                "[RealtimeInputTranscriptionMonitor] Failed to delete remote item "
                "%s: %s",
                item_id,
                exc,
            )

    async def _call_barge_in_callback(self, event: dict):
        cb = (
            self._confirmed_barge_in_cb
            if event.get("is_final")
            else self._partial_barge_in_cb
        )
        if not cb:
            return False
        try:
            result = cb(event)
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)
        except Exception as exc:
            logging.warning(
                "[RealtimeInputTranscriptionMonitor] Confirmed barge-in callback "
                "failed: %s",
                exc,
            )
            return False

    async def _call_voice_assistant_callback(self, event: dict):
        if not self._voice_assistant_cb:
            return
        try:
            result = self._voice_assistant_cb(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logging.warning(
                "[RealtimeInputTranscriptionMonitor] Voice assistant callback "
                "failed: %s",
                exc,
            )


