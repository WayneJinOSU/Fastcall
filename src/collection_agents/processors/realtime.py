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
    LLMFullResponseEndFrame,
    LLMRunFrame,
    LLMTextFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    UserSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def _normalize_lang_code(language: str, default: str = "es") -> str:
    if not language:
        return default
    lang_code = language.split("-")[0].strip().lower()
    if not lang_code:
        return default
    if lang_code == "fil":
        return "tl"
    return lang_code


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


class RealtimeHangupState:
    def __init__(self):
        self.is_hanging_up = False
        self.input_blocked = False
        self.final_phrase_detected = False


class RealtimeHangupGate(FrameProcessor):
    """Stops late user input from reaching Azure Realtime after hangup starts."""

    def __init__(self, hangup_state: RealtimeHangupState):
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


class RealtimeVoicemailDetector(FrameProcessor):
    """Detects voicemail from Azure Realtime upstream transcription frames."""

    def __init__(
        self,
        max_word_count=256,
        language="",
        instant_close=False,
        instant_close_cb=None,
        detected_cb=None,
        voice_assistant_cb=None,
    ):
        super().__init__()
        self._detected = False
        self._instant_close = instant_close
        self._instant_close_cb = instant_close_cb
        self._detected_cb = detected_cb
        self._voice_assistant_cb = voice_assistant_cb
        self._word_buffer = deque(maxlen=max_word_count)

        self._voicemail_regex = REALTIME_VOICEMAIL_REGEX

        lang_code = _normalize_lang_code(language)
        if lang_code == "zh":
            self._prompt = (
                "当前场景：已检测到对方是语音信箱或自动答录机。\n"
                "行动要求：\n"
                "1. 你是来电方，不是语音信箱系统；不要说“无法接通”“稍后再拨”“哔声后留言”等答录机问候语。\n"
                "2. 立即开始留言，内容必须是催收提醒。\n"
                "3. 触发原始 system prompt 中定义的“语音信箱”或“Voicemail”流程。\n"
                "4. 优先使用该流程中的参考留言；如果没有明确参考，简短说明来自平台、请尽快查看账单并完成还款。\n"
                "5. 必须输出再见"
            )
        elif lang_code == "en":
            self._prompt = (
                "Current scenario: Voicemail or automatic answering machine detected.\n"
                "Action requirements:\n"
                "1. You are the caller, not the voicemail system; do not say greetings like unavailable, call later, or leave a message after the tone.\n"
                "2. Leave the debt-collection voicemail immediately.\n"
                "3. Trigger the \"Voicemail\" step defined in the original system prompt.\n"
                "4. Prefer the reference voicemail from that step; if none is explicit, briefly say you are calling from the platform and ask the customer to check the bill and pay as soon as possible.\n"
                "5. You must output Goodbye."
            )
        elif lang_code == "tl":
            self._prompt = (
                "Kasalukuyang senaryo: Na-detect na ang kabilang linya ay voicemail o automatic answering machine.\n"
                "Mga kinakailangang aksyon:\n"
                "1. Ikaw ang tumatawag, hindi ang voicemail system; huwag magsabi ng greeting tulad ng hindi makontak, tumawag muli mamaya, o mag-iwan ng mensahe pagkatapos ng tunog.\n"
                "2. Mag-iwan agad ng maikling voicemail para sa paniningil.\n"
                "3. I-trigger ang \"Voicemail\" step na nakasaad sa orihinal na system prompt.\n"
                "4. Unahin ang reference voicemail sa step na iyon; kung wala, maikling sabihing tumatawag mula sa platform at kailangang tingnan ang bill at magbayad sa lalong madaling panahon.\n"
                "5. Dapat mong ilabas ang Paalam."
            )
        else:
            self._prompt = (
                "Escenario actual: Se ha detectado que el interlocutor es un buzón de voz o un contestador automático.\n"
                "Acción requerida:\n"
                "1. Eres quien llama, no el sistema de buzón; no digas saludos como no disponible, llama más tarde o deja un mensaje después del tono.\n"
                "2. Deja inmediatamente un mensaje breve de cobranza.\n"
                "3. Activa el paso \"Voicemail\" o \"Buzón de voz\" definido en el system prompt original.\n"
                "4. Prioriza el mensaje de referencia de ese paso; si no hay uno explícito, di brevemente que llamas de la plataforma y que revise la cuenta y pague lo antes posible.\n"
                "5. Debes emitir chao."
            )

    async def _call_detected_callback(self):
        if self._detected_cb:
            result = self._detected_cb()
            if asyncio.iscoroutine(result):
                await result

    async def _call_voice_assistant_callback(self, event: dict):
        if not self._voice_assistant_cb:
            return
        result = self._voice_assistant_cb(event)
        if asyncio.iscoroutine(result):
            await result

    async def _push_voicemail_prompt(self, direction: FrameDirection):
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "system", "content": self._prompt}],
                run_llm=True,
            ),
            direction,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            direction == FrameDirection.UPSTREAM
            and isinstance(frame, TranscriptionFrame)
            and not self._detected
        ):
            text = frame.text.strip()
            if text:
                self._word_buffer.extend(text.split())
                check_text = " ".join(self._word_buffer)
                if self._voicemail_regex.search(check_text.lower()):
                    logging.info(
                        "[RealtimeVoicemailDetector] Detected voicemail. "
                        "matched_text=%r current_text=%r",
                        check_text,
                        text,
                    )
                    self._detected = True
                    self._word_buffer.clear()

                    event = {
                        "item_id": RealtimeInputTranscriptionMonitor._extract_item_id(frame),
                        "text": text,
                        "transcript_text": check_text,
                        "check_text": check_text,
                        "classification": "voice_assistant",
                        "classification_detail": "voicemail",
                        "is_barge_in": False,
                        "is_final": True,
                        "action": "detected",
                    }

                    if self._voice_assistant_cb:
                        await self._call_voice_assistant_callback(event)
                        return

                    await self._call_detected_callback()

                    if self._instant_close:
                        logging.info(
                            "[RealtimeVoicemailDetector] Instant close enabled"
                        )
                        if self._instant_close_cb:
                            result = self._instant_close_cb()
                            if asyncio.iscoroutine(result):
                                await result
                        else:
                            await self.push_frame(EndFrame(), direction)
                        return

                    await self._push_voicemail_prompt(direction)
                    return

        await self.push_frame(frame, direction)


class RealtimeWordAwareSanitizer(FrameProcessor):
    """Detects terminal assistant phrases and marks hangup intent.

    Azure Realtime emits duplicate text views for audio responses:
    LLMTextFrame for client events and TTSTextFrame for assistant transcript.
    Only TTSTextFrame is buffered here to avoid doubled text such as
    "HiHi there there".
    """

    def __init__(
        self,
        config_manager,
        transport,
        websocket_client,
        hangup_state: RealtimeHangupState = None,
    ):
        super().__init__()
        self._buffer = ""
        self._max_word_count = 200
        self._config = config_manager
        self._websocket_client = websocket_client
        self._transport = transport
        self._hangup_state = hangup_state
        self._is_hanging_up = False
        self._is_ended = False

        self._endcall_regex = re.compile(
            r"(?is)("
            r"\b(?:goodbye|bye|bye-bye|see you|have a great day|have a good one|"
            r"adi[oó]s|hasta\s+(?:luego|mañana|pronto)|nos\s+vemos|chao|chau|cu[ií]date|"
            r"paalam|hanggang\s+sa\s+muli|ingat\s+ka|"
            r"que\s+tenga\s+(?:un\s+)?(?:lindo|buen|excelente)\s+d[ií]a)\b|"
            r"再见|拜拜|挂了|"
            r"\[\[HANGUP\]\]"
            r")\s*[\]）)]?[\s。.!！?？,，、;；:：\"'”’）)]*$"
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self._is_hanging_up:
            if isinstance(frame, (TextFrame, InterruptionFrame)):
                return
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMTextFrame):
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TTSTextFrame):
            self._buffer += frame.text
            if len(self._buffer.strip().split()) >= self._max_word_count:
                self._buffer = self._buffer[-1000:]
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._buffer.strip():
                await self._sanitize_and_push(self._buffer, direction)
                self._buffer = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, EndFrame):
            if self._buffer.strip():
                await self._sanitize_and_push(self._buffer, direction)
                self._buffer = ""
            self._is_ended = True
            logging.info("[RealtimeWordAwareSanitizer] Pushing EndFrame: %s", frame)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _sanitize_and_push(self, text: str, direction: FrameDirection):
        clean_text = text.strip()
        if not clean_text:
            return

        logging.debug(
            "[RealtimeWordAwareSanitizer] checking assistant transcript buffer: %r",
            clean_text[:120],
        )
        if self._endcall_regex.search(clean_text) and not self._is_hanging_up:
            logging.info(
                "[RealtimeWordAwareSanitizer] Detected tail end phrase; "
                "marking graceful hangup intent."
            )
            logging.info("trigger push")
            self._is_hanging_up = True
            if self._hangup_state is not None:
                self._hangup_state.is_hanging_up = True
                self._hangup_state.final_phrase_detected = True


class GracefulHangupController(FrameProcessor):
    """Hangs up after terminal assistant audio finishes, with a fallback timer."""

    def __init__(
        self,
        *,
        hangup_state: RealtimeHangupState,
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
            "realtime_graceful_hangup_fallback",
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
            "realtime_graceful_hangup_after_bot_stopped",
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
