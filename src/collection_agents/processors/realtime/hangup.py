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

from collection_agents.processors.common.hangup import GracefulHangupController, HangupState


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
