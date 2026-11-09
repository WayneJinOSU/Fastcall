import asyncio
import logging
import re
import time
from collections import deque

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMMessagesAppendFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSTextFrame,
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


class VoicemailDetector(FrameProcessor):
    def __init__(
        self,
        max_word_count=30,
        language="",
        instant_close=False,
        instant_close_cb=None,
        detected_cb=None,
        finalize_stt_cb=None,
        fixed_voicemail_text=None,
        fixed_voicemail_live_text=None,
        fixed_voicemail_audio_lookup=None,
        fixed_voicemail_done_cb=None,
        fixed_voicemail_audio_done_padding=0.3,
        voice_assistant_cb=None,
        mode="standard",
    ):
        super().__init__()
        if mode not in {"standard", "realtime"}:
            raise ValueError(f"Unsupported voicemail detector mode: {mode}")
        self._realtime = mode == "realtime"
        self._detected = False
        self._pending = False
        self._awaiting_fixed_voicemail_live_stop = False
        self._awaiting_fixed_voicemail_stop = False
        self._bot_speaking = False
        self._fixed_voicemail_done_task = None
        self._last_finalize_requested_at = None
        self._instant_close = instant_close
        self._instant_close_cb = instant_close_cb
        self._detected_cb = detected_cb
        self._voice_assistant_cb = voice_assistant_cb
        self._finalize_stt_cb = finalize_stt_cb
        self._fixed_voicemail_text = (fixed_voicemail_text or "").strip()
        self._fixed_voicemail_live_text = (fixed_voicemail_live_text or "").strip()
        self._fixed_voicemail_audio_lookup = fixed_voicemail_audio_lookup
        self._fixed_voicemail_done_cb = fixed_voicemail_done_cb
        self._fixed_voicemail_audio_done_padding = fixed_voicemail_audio_done_padding
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

        lang_code = _normalize_lang_code(language)
        if self._realtime:
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
        elif lang_code == "zh":
            self._prompt = (
                "当前场景：已检测到对方是语音信箱或自动答录机。\n"
                "行动要求：\n"
                "1. 忽略之前所有的对话上下文（包括对方可能发出的'喂'等声音）。\n"
                "2. 立即开始留言。\n"
                "3. 触发流程 : 检测到语音信箱 \n"
                "4. 语言要简练。\n"
                "5. 重要：在留言内容的最后，必须且只能输出这个标签用于挂断电话：再见"
            )
        elif lang_code == "en":
            self._prompt = (
                "Current scenario: Voicemail or automatic answering machine detected.\n"
                "Action requirements:\n"
                "1. Ignore all previous conversation context (including sounds like 'hello' that the other party might have made).\n"
                "2. Start leaving a message immediately.\n"
                "3. Trigger flow : Voicemail detected \n"
                "4. Keep the language concise.\n"
                "5. Important: At the end of the message content, you must and can only output this tag to hang up the call: Goodbye"
            )
        elif lang_code == "tl":
            self._prompt = (
                "Kasalukuyang senaryo: Na-detect na ang kabilang linya ay voicemail o automatic answering machine.\n"
                "Mga kinakailangang aksyon:\n"
                "1. Balewalain ang lahat ng naunang konteksto ng usapan (kasama ang mga tunog tulad ng 'hello' na maaaring ginawa ng kabilang linya).\n"
                "2. Agad na mag-iwan ng mensahe.\n"
                "3. I-trigger ang proseso : Na-detect ang voicemail \n"
                "4. Panatilihing maikli at malinaw ang pananalita.\n"
                "5. Mahalaga: Sa dulo ng nilalaman ng mensahe, dapat at tanging ang tag na ito lamang ang ilalabas upang ibaba ang tawag: Paalam"
            )
        else:
            self._prompt = (
                "Escenario actual: Se ha detectado que el interlocutor es un buzón de voz o un contestador automático.\n"
                "Acción requerida:\n"
                "1. Ignora cualquier input anterior del usuario.\n"
                "2. Deja un mensaje de voz inmediatamente.\n"
                "3. Activar el proceso: Buzón de voz detectado\n"
                "4. Sé breve.\n"
                "5. IMPORTANTE: Al finalizar tu mensaje, DEBES escribir exactamente esta etiqueta para colgar: chao"
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

    async def _call_fixed_voicemail_done_callback(self):
        if self._fixed_voicemail_done_cb:
            result = self._fixed_voicemail_done_cb()
            if asyncio.iscoroutine(result):
                await result

    async def _lookup_fixed_voicemail_audio(self):
        if not self._fixed_voicemail_audio_lookup:
            return None

        try:
            result = self._fixed_voicemail_audio_lookup()
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:
            logging.warning(
                "[VoicemailDetector] Fixed voicemail audio lookup failed: %s",
                exc,
            )
            return None

        if isinstance(result, OutputAudioRawFrame):
            return {"frame": result, "asset": None}

        if not isinstance(result, dict):
            logging.warning(
                "[VoicemailDetector] Fixed voicemail audio lookup returned unsupported payload: %s",
                type(result).__name__,
            )
            return None

        frame = result.get("frame") or result.get("audio_frame")
        if not isinstance(frame, OutputAudioRawFrame):
            logging.warning(
                "[VoicemailDetector] Fixed voicemail audio lookup returned no OutputAudioRawFrame."
            )
            return None

        return {
            "frame": frame,
            "asset": result.get("asset"),
        }

    def _estimate_audio_duration_secs(self, frame: OutputAudioRawFrame) -> float:
        sample_rate = getattr(frame, "sample_rate", None) or 0
        num_frames = getattr(frame, "num_frames", None) or 0
        if sample_rate <= 0 or num_frames <= 0:
            return 0.0
        return num_frames / sample_rate

    def _cancel_fixed_voicemail_done_fallback(self):
        task = self._fixed_voicemail_done_task
        if task and not task.done():
            task.cancel()
        self._fixed_voicemail_done_task = None

    async def cleanup(self):
        await super().cleanup()
        self._cancel_fixed_voicemail_done_fallback()

    def _schedule_fixed_voicemail_done_fallback(self, frame: OutputAudioRawFrame):
        self._cancel_fixed_voicemail_done_fallback()
        delay_secs = (
            self._estimate_audio_duration_secs(frame)
            + self._fixed_voicemail_audio_done_padding
        )
        self._fixed_voicemail_done_task = asyncio.create_task(
            self._fixed_voicemail_done_fallback(delay_secs)
        )

    async def _fixed_voicemail_done_fallback(self, delay_secs: float):
        try:
            await asyncio.sleep(max(delay_secs, 0.0))
            if not self._awaiting_fixed_voicemail_stop:
                return
            self._awaiting_fixed_voicemail_stop = False
            logging.warning(
                "[VoicemailDetector] Fixed voicemail audio finished by duration fallback."
            )
            await self._call_fixed_voicemail_done_callback()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.warning(
                "[VoicemailDetector] Fixed voicemail audio done fallback failed: %s",
                exc,
            )

    async def _push_fixed_voicemail_context(self, direction: FrameDirection):
        voicemail_text = " ".join(
            text
            for text in (
                self._fixed_voicemail_live_text,
                self._fixed_voicemail_text,
            )
            if text
        )
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[
                    {
                        "role": "assistant",
                        "content": voicemail_text,
                    }
                ],
                run_llm=False,
            ),
            direction,
        )

    async def _push_cached_voicemail_segment(self, direction: FrameDirection):
        if not self._fixed_voicemail_text:
            await self._call_fixed_voicemail_done_callback()
            return

        audio_payload = await self._lookup_fixed_voicemail_audio()
        if audio_payload:
            audio_frame = audio_payload["frame"]
            asset = audio_payload.get("asset") or {}
            logging.info(
                "[VoicemailDetector] Queueing fixed voicemail local audio. "
                "text_hash=%s file_path=%s",
                asset.get("text_hash"),
                asset.get("file_path"),
            )
            self._awaiting_fixed_voicemail_stop = True
            await self.push_frame(audio_frame, direction)
            self._schedule_fixed_voicemail_done_fallback(audio_frame)
            return

        logging.info("[VoicemailDetector] Queueing fixed voicemail cache segment without LLM.")
        self._awaiting_fixed_voicemail_stop = True
        await self.push_frame(
            TTSSpeakFrame(
                self._fixed_voicemail_text,
                append_to_context=False,
            ),
            direction,
        )

    async def _push_voicemail_prompt(self, direction: FrameDirection):
        if self._fixed_voicemail_live_text or self._fixed_voicemail_text:
            await self._push_fixed_voicemail_context(direction)
            if self._fixed_voicemail_live_text:
                logging.info(
                    "[VoicemailDetector] Queueing live voicemail first segment without LLM. "
                    "text_length=%s",
                    len(self._fixed_voicemail_live_text),
                )
                self._awaiting_fixed_voicemail_live_stop = True
                await self.push_frame(
                    TTSSpeakFrame(
                        self._fixed_voicemail_live_text,
                        append_to_context=False,
                    ),
                    direction,
                )
                return

            await self._push_cached_voicemail_segment(direction)
            return

        logging.warning(
            "[VoicemailDetector] No fixed voicemail message configured; falling back to LLM voicemail prompt."
        )
        await self.push_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "system", "content": self._prompt}],
                run_llm=True,
            ),
            direction,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._realtime and isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True

        elif not self._realtime and isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            if self._pending:
                self._pending = False
                logging.info(
                    "[VoicemailDetector] Bot stopped speaking, triggering deferred voicemail message."
                )
                await self._push_voicemail_prompt(FrameDirection.DOWNSTREAM)
            elif self._awaiting_fixed_voicemail_live_stop:
                self._awaiting_fixed_voicemail_live_stop = False
                logging.info(
                    "[VoicemailDetector] Live voicemail first segment completed; queueing cache segment."
                )
                await self._push_cached_voicemail_segment(FrameDirection.DOWNSTREAM)
            elif self._awaiting_fixed_voicemail_stop:
                self._cancel_fixed_voicemail_done_fallback()
                self._awaiting_fixed_voicemail_stop = False
                logging.info("[VoicemailDetector] Fixed voicemail message completed.")
                await self._call_fixed_voicemail_done_callback()

        else:
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
                    if self._voicemail_regex.search(check_text.lower()):
                        logging.info(
                            "[VoicemailDetector] Detected keywords in word-window. "
                            "mode=%s frame_type=%s matched_text=%r current_text=%r",
                            "realtime" if self._realtime else "standard",
                            type(frame).__name__,
                            check_text,
                            text,
                        )
                        self._detected = True
                        self._word_buffer.clear()

                        if self._realtime and self._voice_assistant_cb:
                            result = getattr(frame, "result", None)
                            event = {
                                "item_id": str(getattr(result, "item_id", "") or ""),
                                "text": text,
                                "transcript_text": check_text,
                                "check_text": check_text,
                                "classification": "voice_assistant",
                                "classification_detail": "voicemail",
                                "is_barge_in": False,
                                "is_final": True,
                                "action": "detected",
                            }
                            await self._call_voice_assistant_callback(event)
                            return

                        if not self._realtime:
                            self._last_finalize_requested_at = time.monotonic()
                        if self._finalize_stt_cb:
                            try:
                                result = self._finalize_stt_cb()
                                if asyncio.iscoroutine(result):
                                    await result
                                logging.info(
                                    "[VoicemailDetector] Triggered STT finalize after voicemail detection. "
                                    "frame_type=%s",
                                    type(frame).__name__,
                                )
                            except Exception as exc:
                                logging.warning(
                                    f"[VoicemailDetector] Failed to trigger STT finalize: {exc}"
                                )
                        await self._call_detected_callback()
                        if self._instant_close:
                            logging.info(
                                "[VoicemailDetector] Instant close enabled; "
                                "skipping voicemail message flow."
                            )
                            if self._instant_close_cb:
                                result = self._instant_close_cb()
                                if asyncio.iscoroutine(result):
                                    await result
                            else:
                                await self.push_frame(EndFrame(), direction)
                            return
                        if not self._realtime and self._bot_speaking:
                            self._pending = True
                            logging.info(
                                "[VoicemailDetector] Detected during bot speech, deferring until BotStoppedSpeakingFrame."
                            )
                        else:
                            logging.info(
                                "[VoicemailDetector] Detected keywords, triggering voicemail message immediately."
                            )
                            await self._push_voicemail_prompt(direction)
                        return

            elif isinstance(frame, TranscriptionFrame) and self._last_finalize_requested_at is not None:
                elapsed_ms = (time.monotonic() - self._last_finalize_requested_at) * 1000
                logging.info(
                    "[VoicemailDetector] Final transcript arrived after forced finalize: "
                    "elapsed_ms=%.1f current_text=%r",
                    elapsed_ms,
                    frame.text,
                )
                self._last_finalize_requested_at = None

        if isinstance(frame, (EndFrame, CancelFrame)):
            self._cancel_fixed_voicemail_done_fallback()
        if isinstance(frame, EndFrame):
            logging.info(f"[VoicemailDetector] Pushing EndFrame: {frame}")
        await self.push_frame(frame, direction)


class WordAwareSanitizer(FrameProcessor):
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
        self._config = config_manager
        self._websocket_client = websocket_client
        self._transport = transport
        self._exit_delay = exit_delay
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
                self._is_ended = True
                logging.info("[WordAwareSanitizer] Pushing EndFrame: %s", frame)
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _sanitize_and_push(self, text: str, direction: FrameDirection):
        clean_text = text.strip()
        if not clean_text:
            return

        logging.debug(
            "[WordAwareSanitizer] checking assistant transcript buffer: %r",
            clean_text[:120],
        )
        if self._endcall_regex.search(clean_text) and not self._is_hanging_up:
            logging.info(
                "[WordAwareSanitizer] Detected tail end phrase; "
                "marking graceful hangup intent."
            )
            self._is_hanging_up = True
            if self._hangup_state is not None:
                self._hangup_state.is_hanging_up = True
                self._hangup_state.final_phrase_detected = True
