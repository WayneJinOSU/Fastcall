import argparse
import asyncio
import aiohttp
import datetime
import logging
import os
import time
import traceback
import json
from functools import partial
from types import MethodType
from dotenv import load_dotenv
from my_utils.utc2tz import utc2tz
from my_utils.websocket_beta import (
    CustomProtocolFrameSerializer,
    TelephonyEventRouter,
)
from my_utils.websocket_safe_send import (
    harden_pipecat_websocket_transport,
    safe_send_bytes,
    safe_send_text,
)
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport
)
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat import __version__

print(f"pipecat version: {__version__}")

from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_mute import (
    AlwaysUserMuteStrategy,
    FunctionCallUserMuteStrategy,
)
from processors_common import ContextMessageTimestampTracker, SilenceDetector
from processors_beta import (
    TranscriptionLogger,
    FrameLogger,
    GracefulHangupController,
    HangupGate,
    HangupState,
    OpeningSilenceTimeoutProcessor,
    ReplyProtectionInterruptionGate,
    STTGatedInterruptor,
    ContextSafeAssistantTurnGate,
    STTPreRollMuteProcessor,
    WordAwareSanitizer,
    VoicemailDetector,
)

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    EndFrame,
    StartFrame,
)

from pipecat.observers.base_observer import BaseObserver

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from call_metrics.collector import CallMetricsCollector, CallMetricsObserver
from bot_beta_modules.config import (
    BotV3ConfigManager,
    coerce_positive_int,
    create_system_prompt,
    resolve_task_type,
    is_voicemail_hangup_action,
    prepare_text_for_tts,
)
from bot_beta_modules.audio_assets import (
    DEFAULT_CARTESIA_VOICE_ID,
    DEFAULT_LOCALE,
    DEFAULT_MODEL,
    SAMPLE_RATE as AUDIO_ASSET_SAMPLE_RATE,
    TEXT_TYPE_VOICEMAIL,
    ensure_voicemail_audio_asset,
    find_audio_asset,
    load_audio_frame,
    normalize_text as normalize_audio_asset_text,
    split_voicemail_segments,
)
from bot_beta_modules.notes import intelligent_notes_distribution, save_conversation
from bot_beta_modules.prompt_client import resolve_collection_harness_llm_prompt
from bot_beta_modules.outbound_callback import send_call_data_callback
from bot_beta_modules.services import (
    attach_llm_retry_counter,
    create_llm_service,
    create_stt_service,
    create_tts_service,
    load_output_sounds,
    resolve_bot_llm_runtime,
    resolve_deepgram_model,
    resolve_deepgram_shadow_config,
    resolve_stt_provider,
    send_hangup_signal,
)
from bot_beta_modules.shadow_stt import (
    DeepgramShadowDecoder,
    ShadowDecodeTap,
    VoskFinalCollector,
)
from bot_beta_modules.asr_compare import save_asr_shadow_compare


sounds = load_output_sounds(os.path.dirname(__file__))
load_dotenv(override=True)

WELCOME_AUDIO_PREROLL_MS = 500

# Confirmation-turn grammar for Vosk: constrains decoding to the closed set of
# answers the identity-confirmation question can get, which fixes the small
# model's worst errors ("Sí, soy yo." -> "si suyo" in free decoding).
# Unaccented on purpose: the model vocabulary has no accents, accented words
# are silently dropped from the grammar. '[unk]' is prepended automatically by
# VoskSTTService so off-list speech still decodes instead of failing.
CONFIRMATION_GRAMMAR = [
    "si", "no", "si soy yo", "no no soy yo",
    "es mi esposo", "es mi esposa", "es mi hijo", "es mi hija",
    "un momento", "quien es", "quien habla", "diga", "digame", "bueno", "hola", "mande",
    "me equivoque de numero", "no vive aqui", "estoy ocupado", "llame mas tarde",
    "ya pague", "puedo hablar", "correcto", "exacto", "claro", "como no",
]
# Fallback release: if the user never answers, free decoding must come back so
# later open conversation is not forced onto the phrase list.
CONFIRMATION_GRAMMAR_RELEASE_SEC = 20.0

# Pre-normalized grammar set for the conditional release check: a final
# transcript that still lands inside the confirmation phrase set means the
# customer is still answering closed questions (e.g. "hola" then "si"), so
# the grammar must stay armed for the next answer too. Only free-form
# speech outside the set triggers the release. Normalization mirrors the
# unaccented/lowercase vocabulary: strip accents, punctuation and case.
import re as _re
import unicodedata as _unicodedata

_GRAMMAR_PHRASES = set(CONFIRMATION_GRAMMAR)


def _normalize_for_grammar(text: str) -> str:
    text = _unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if _unicodedata.category(ch) != "Mn")
    return " ".join(_re.findall(r"[a-z0-9]+", text))


def _is_confirmation_phrase(text: str) -> bool:
    """True if the transcript is fully covered by grammar-listed phrases.

    Segmentation match (word sequence covered end-to-end by listed phrases,
    e.g. "bueno si" -> "bueno" + "si") instead of exact match: the small
    model may attach or drop fillers around a listed phrase, and full
    coverage still means we are inside the confirmation window. Anything
    with an off-list word (real conversation) returns False.
    """
    normalized = _normalize_for_grammar(text)
    if not normalized:
        return False
    words = normalized.split()
    max_phrase_words = max(len(p.split()) for p in _GRAMMAR_PHRASES)
    # reachable[i]: words[:i] fully covered by grammar phrases
    reachable = [False] * (len(words) + 1)
    reachable[0] = True
    for i in range(len(words)):
        if not reachable[i]:
            continue
        for n in range(1, min(max_phrase_words, len(words) - i) + 1):
            if " ".join(words[i:i + n]) in _GRAMMAR_PHRASES:
                reachable[i + n] = True
    return reachable[len(words)]


def _get_env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _build_silence_frame(duration_ms: int, sample_rate: int = 8000, channels: int = 1):
    if duration_ms <= 0:
        return None
    sample_width = 2
    frame_count = int(sample_rate * duration_ms / 1000)
    return OutputAudioRawFrame(
        audio=b"\x00" * frame_count * channels * sample_width,
        sample_rate=sample_rate,
        num_channels=channels,
    )


class PipelineFlowWatchdog(BaseObserver):
    """Logs an ERROR if the pipeline produces no observable frame flow shortly after start.

    Some failed calls show only "Queue fixed welcome TTS" and then nothing — no
    frame summaries, no teardown logs — which is indistinguishable from a hung
    pipeline or a dead pod from the business logs alone. This observer arms on
    pipeline start and fires once if no frame reaches the transport sink within
    `timeout_sec`, so the next such call is immediately classifiable.

    StartFrame reaching the sink only proves the pipeline is wired up; the
    welcome TTS is queued seconds later (on_answer), and a stall there leaves no
    trace at all. So the watchdog re-arms on `rearm()` (called from _on_answer)
    and only disarms once the first bot audio frame (TTSAudioRawFrame) reaches
    the sink, i.e. the bot actually started speaking.
    """

    def __init__(self, timeout_sec: float = 10.0):
        super().__init__()
        self._timeout_sec = timeout_sec
        self._armed = False
        self._fired = False
        self._watch_task: asyncio.Task | None = None

    def rearm(self):
        """Re-arm the watchdog when a new expected-flow point is reached (on_answer)."""
        if self._fired:
            return
        self._armed = True
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = asyncio.ensure_future(self._watch_loop())

    async def on_push_frame(self, data):
        if self._fired:
            return
        if not self._armed and isinstance(data.frame, StartFrame):
            self._armed = True
            self._watch_task = asyncio.ensure_future(self._watch_loop())
            return
        if self._armed and data.destination is None:
            # Frame reached the pipeline sink. Only bot audio counts as real
            # flow: control frames (e.g. the StartFrame echo) reach the sink
            # long before the welcome TTS is queued.
            if isinstance(data.frame, TTSAudioRawFrame):
                self._disarm()

    async def _watch_loop(self):
        try:
            await asyncio.sleep(self._timeout_sec)
            if self._armed and not self._fired:
                self._fired = True
                logging.error(
                    "[pipeline_watchdog] no bot audio frame reached the pipeline sink %ss after "
                    "pipeline start/answer; pipeline may be stalled or the pod dead",
                    self._timeout_sec,
                )
        except asyncio.CancelledError:
            raise

    def _disarm(self):
        self._armed = False
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
        self._watch_task = None

    async def cleanup(self):
        self._disarm()


async def run_bot_beta(websocket_client, stream_sid, call_data, call_timeout=180):
    """Run bot with v3 configuration system - fixes assistant message recording"""
    session_context = {}
    context = None
    tts_config = {}
    call_uuid = ""
    task = None
    duration_monitor = None
    flow_watchdog = None
    work_order_id = ""
    metrics_collector = None
    llm_service = None
    # Deepgram shadow (A/B comparison vs live Vosk). Created only when the
    # robot runs Vosk AND shadow config resolves; kept in the outer scope so
    # the finally block can stop it and persist the comparison row.
    shadow_config = None
    shadow_decoder = None
    vosk_final_collector = None

    try:
        config_manager = BotV3ConfigManager(call_data)
        # Extract callUUID from call_data
        call_uuid = call_data.get("data", {}).get("callUUID", "")
        business_id = call_data.get("data", {}).get("businessID", "")

        variables = call_data.get("data", {}).get("config", {}).get("variables", {})
        country = variables.get("country", "")
        task_type = variables.get("task_type", "collection")
        voicemail_config = config_manager.get_voicemail_config()
        voicemail_action = (voicemail_config.get("action") or "").strip()
        voicemail_hangup_on_detect = is_voicemail_hangup_action(voicemail_config)

        follower_id = variables.get("follower_id", "待传入")
        dial_schedule = variables.get("dial_schedule", ["待传入"])
        customer_group = variables.get("customer_group", "待传入")
        has_sms = variables.get("has_sms", 0) #待传入
        has_app_push = variables.get("has_app_push", 0) #待传入

        contact_time = utc2tz(time.time(), country)
        variables["contact_time"] = contact_time

        tenant_id = variables.get("tenant_id", "")
        app_type = variables.get("app_type", "")
        work_order_id = variables.get("work_order_id", "") #工单id
        outbound_source = variables.get("outbound_source") or "hermes"

        platform = "CPU"
        is_round_robin = int(1) #是否轮询
        is_human_assist = int(0) #是否人机协作

        robot_code = call_data.get("data", {}).get("config", {}).get("code")
        robot_name = call_data.get("data", {}).get("config", {}).get("name")
        resolved_task_type = resolve_task_type(task_type, robot_name)
        if resolved_task_type != task_type:
            logging.info(
                "Task type resolved from robot name: robot_name=%r task_type=%s",
                robot_name,
                resolved_task_type,
            )
        task_type = resolved_task_type
        # intelligent_notes_distribution reads the value from call_data, not
        # the local variable, so keep both sources consistent.
        variables["task_type"] = task_type
        org_code = call_data.get("data", {}).get("config", {}).get("orgCode")
        org_name = call_data.get("data", {}).get("config", {}).get("orgName")

        async with aiohttp.ClientSession() as session:
            session_context = {
                "websocket_client": websocket_client,
                "stream_sid": stream_sid,
                "on_answer": False,
                "is_end": False,
                "call_start_time": time.time(),
                "last_user_stop_time": None,
                "last_bot_speak_time": time.time(),
                "silence_check_task": None,
                "config_manager": config_manager,
                "no_response_count": 0,
                "no_response_hangup_queued": False,
                "no_response_hangup_task": None,
                "voicemail_detected": False,
                "voicemail_hangup_task": None,
                "voicemail_audio_playback": None,
                "assistant_response_count": 0
            }

            logging.info(f"Call data config: {config_manager.config}")
            logging.info(
                "Voicemail node action=%r, hangup on detection: %s",
                voicemail_action,
                voicemail_hangup_on_detect,
            )

            # Create services based on configuration
            asr_config = config_manager.get_asr_config()
            tts_config = config_manager.get_tts_config()
            llm_config = config_manager.get_llm_config()

            # Render voicemail templates before splitting it into the live first
            # sentence and the cacheable remainder.  The cache builder applies the
            # same transformation so its text hash matches this runtime lookup.
            voicemail_content = normalize_audio_asset_text(
                prepare_text_for_tts(
                    voicemail_config.get("content") or "",
                    tts_config,
                    variables,
                )
            )
            fixed_voicemail_live_text, fixed_voicemail_text = split_voicemail_segments(
                voicemail_content
            )
            fixed_voicemail_cache_enabled = bool(fixed_voicemail_text)
            if voicemail_hangup_on_detect:
                voicemail_mode = "hangup"
            elif fixed_voicemail_live_text or fixed_voicemail_text:
                voicemail_mode = "fixed_message"
            else:
                voicemail_mode = "legacy_llm"

            logging.info("Voicemail handling mode=%s", voicemail_mode)
            if voicemail_mode == "legacy_llm":
                logging.info(
                    "flowConfig.voicemailNode.content is empty; using the legacy LLM voicemail flow."
                )
            elif not fixed_voicemail_text:
                logging.info(
                    "Voicemail message has no cacheable second segment; using live TTS only."
                )
            else:
                asyncio.create_task(ensure_voicemail_audio_asset(call_data))

            stt_provider = resolve_stt_provider(asr_config, robot_code)
            stt_service = create_stt_service(
                asr_config,
                robot_code,
                robot_name,
                grammar=CONFIRMATION_GRAMMAR if stt_provider == "vosk" else None,
            )
            # Shadow Deepgram decoding for Vosk calls: same audio is sent to
            # Deepgram in parallel purely for offline A/B accuracy comparison.
            # Never touches the live path (no frames pushed, no interruption).
            if stt_provider == "vosk":
                shadow_config = resolve_deepgram_shadow_config(asr_config, robot_code)
                if shadow_config:
                    shadow_decoder = DeepgramShadowDecoder(**shadow_config)
                    vosk_final_collector = VoskFinalCollector()
                    logging.info(
                        "[ShadowSTT] armed for Vosk call: deepgram model=%s language=%s",
                        shadow_config.get("model"),
                        shadow_config.get("language"),
                    )
                else:
                    logging.info("[ShadowSTT] not available (no API key/SDK), skipping")
            # The confirmation grammar is armed from call start (the first user
            # utterance is the answer to the welcome confirmation question).
            # Release is conditional: only an off-grammar final transcript (real
            # free-form conversation) releases it; in-grammar answers ("hola",
            # "bueno", "si") keep it armed so follow-up short answers are still
            # decoded under the phrase list. A rolling timer is the fallback
            # release for calls where the customer never goes off-grammar.
            session_context["confirmation_grammar_active"] = stt_provider == "vosk"
            session_context["last_transcription_at"] = 0.0

            def _cancel_grammar_release_task():
                release_task = session_context.get("confirmation_grammar_release_task")
                if release_task and not release_task.done():
                    release_task.cancel()
                session_context["confirmation_grammar_release_task"] = None

            async def _release_confirmation_grammar(reason: str):
                """Switch Vosk back to free decoding after the confirmation turn."""
                if not session_context.get("confirmation_grammar_active"):
                    return
                session_context["confirmation_grammar_active"] = False
                _cancel_grammar_release_task()
                if hasattr(stt_service, "set_grammar"):
                    await stt_service.set_grammar(None)
                logging.info("Confirmation grammar released: %s", reason)

            async def _grammar_release_timer():
                try:
                    while True:
                        await asyncio.sleep(CONFIRMATION_GRAMMAR_RELEASE_SEC)
                        if not session_context.get("confirmation_grammar_active"):
                            return
                        # Roll the window forward while the customer is still
                        # answering (in-grammar finals keep arriving); only
                        # release after a full quiet interval.
                        idle_for = (
                            time.monotonic()
                            - session_context.get("last_transcription_at", 0.0)
                        )
                        if idle_for < CONFIRMATION_GRAMMAR_RELEASE_SEC:
                            logging.info(
                                "Confirmation grammar release timer extended: "
                                "user active %.1fs ago",
                                idle_for,
                            )
                            continue
                        await _release_confirmation_grammar("release timeout")
                        return
                except asyncio.CancelledError:
                    pass

            def _rearm_grammar_release_timer():
                """Restart the rolling release timer after an in-grammar answer."""
                if not session_context.get("confirmation_grammar_active"):
                    return
                _cancel_grammar_release_task()
                session_context["confirmation_grammar_release_task"] = (
                    asyncio.create_task(_grammar_release_timer())
                )
            tts_service = create_tts_service(tts_config, session)

            # Cartesia's own connect logs are DEBUG-only: without these, a failed
            # TTS websocket is invisible at INFO level and the call looks like a
            # silent bot.
            @tts_service.event_handler("on_connected")
            async def _tts_on_connected(service):
                logging.info("[tts] websocket connected: %s", type(service).__name__)

            @tts_service.event_handler("on_connection_error")
            async def _tts_on_connection_error(service, error):
                logging.error("[tts] websocket connection error: %s: %s", type(service).__name__, error)

            @tts_service.event_handler("on_disconnected")
            async def _tts_on_disconnected(service):
                logging.info("[tts] websocket disconnected: %s", type(service).__name__)
            llm_config, platform = resolve_bot_llm_runtime(
                llm_config,
                robot_name,
                robot_code,
            )
            # The websocket prompt is the fail-open fallback for harness
            # lookup failures, so a temporary prompt API issue cannot stop a call.
            websocket_prompt = (
                llm_config.get("prompt") or config_manager.get_prompt_config() or ""
            )
            prompt_uuid = await resolve_collection_harness_llm_prompt(
                outbound_source=outbound_source,
                variables=variables,
                llm_config=llm_config,
                fallback_prompt=websocket_prompt,
            )
            llm_service = create_llm_service(llm_config)

            if asr_config.get("provider") == "cartesia":
                _asr_model = asr_config.get("model", "ink-whisper")
            else:
                _asr_model = resolve_deepgram_model(robot_code)
            llm_metrics_config = dict(llm_config)
            if prompt_uuid:
                # The harness prompt is for this attempt only.  Keep it out of
                # process_record and the completed-call business payload.
                llm_metrics_config.pop("prompt", None)
                llm_metrics_config.pop("api_key", None)
            metrics_collector = CallMetricsCollector(
                call_uuid=call_uuid,
                business_id = business_id,
                tenant_id = tenant_id,
                app_type = app_type,
                work_order_id = work_order_id,
                outbound_source = outbound_source,
                platform = platform,
                is_round_robin = is_round_robin,
                is_human_assist = is_human_assist,
                robot_code = robot_code,
                robot_name = robot_name,
                org_code = org_code,
                org_name = org_name,
                country = country,
                task_type = task_type,
                follower_id = follower_id,
                dial_schedule = dial_schedule,
                customer_group = customer_group,
                has_sms = has_sms,
                has_app_push = has_app_push,
                task_start_time=datetime.datetime.now(),
                asr_info={**asr_config, "model": _asr_model, "sample_rate": 8000},
                llm_info=llm_metrics_config,
                llm_prompt="" if prompt_uuid else llm_config.get("prompt", ""),
                prompt_uuid=prompt_uuid,
                tts_info={**tts_config, "sample_rate": 8000},
                # pricing_config overrides _DEFAULT_PRICING in collector.py.
                # Default covers Deepgram, Cartesia STT/TTS, and supported LLMs.
                # Uncomment and adjust if using a different model or pricing tier.
                # pricing_config={
                #     "asr_rate_per_hour": 0.31,   # e.g. nova-3 monolingual
                # },
            )
            attach_llm_retry_counter(llm_service, metrics_collector)

            # Track TTS reconnects via on_connected event (fires on initial connect
            # and every subsequent reconnect; skip the first to count only reconnects).
            _tts_connected_once = False

            @tts_service.event_handler("on_connected")
            async def _on_tts_connected(service):
                nonlocal _tts_connected_once
                if _tts_connected_once:
                    metrics_collector.increment_tts_reconnect()
                else:
                    _tts_connected_once = True

            # Track ASR reconnects as: after a Deepgram connection error, the next
            # successful _connect() counts as one reconnect. Initial connect is not counted.
            if hasattr(stt_service, "_connect") and hasattr(stt_service, "_on_error"):
                _stt_reconnect_state = {
                    "saw_connection_error": False,
                }
                _original_stt_connect = stt_service._connect
                _original_stt_on_error = stt_service._on_error

                async def _tracked_stt_connect(self, *args, **kwargs):
                    await _original_stt_connect(*args, **kwargs)
                    if _stt_reconnect_state["saw_connection_error"]:
                        metrics_collector.increment_asr_reconnect()
                        logging.info(
                            "ASR reconnect counted after Deepgram connection error. asr_reconnect_count=%d",
                            metrics_collector.asr_reconnect_count,
                        )
                        _stt_reconnect_state["saw_connection_error"] = False

                async def _tracked_stt_on_error(self, *args, **kwargs):
                    _stt_reconnect_state["saw_connection_error"] = True
                    logging.warning("Deepgram connection error detected; next successful _connect will count as ASR reconnect")
                    await _original_stt_on_error(*args, **kwargs)

                stt_service._connect = MethodType(_tracked_stt_connect, stt_service)
                stt_service._on_error = MethodType(_tracked_stt_on_error, stt_service)

            # 🔍 服务配置详细调试日志
            logging.info(
                f"🔧 TTS服务配置: provider={tts_config.get('provider')}, voice_id={tts_config.get('voice_id')}, model={tts_config.get('model')}, locale={tts_config.get('locale')}")
            logging.info(f"🔧 ASR服务配置: provider={asr_config.get('provider')}, locale={asr_config.get('locale')}")
            logging.info(f"🔧 LLM服务配置: provider={llm_config.get('provider')}, model={llm_config.get('model')}")
            logging.info("🎯 关键修复: TTS服务直接输出8kHz音频，消除24kHz->8kHz实时降采样造成的音质劣化")

            # 🔍 TTS服务实例详细信息
            if hasattr(tts_service, '_sample_rate'):
                logging.info(f"🎵 TTS服务实例采样率: {tts_service._sample_rate}Hz")
            if hasattr(tts_service, 'model_name'):
                logging.info(f"🎵 TTS服务模型: {tts_service.model_name}")
            else:
                logging.info("🎵 TTS服务模型信息不可用")

            logging.info(f"🎵 TTS服务类型: {type(tts_service).__name__}")
            logging.info(f"🎵 TTS服务父类: {[cls.__name__ for cls in type(tts_service).__mro__[1:3]]}")

            allow_interruptions = _get_env_bool("ALLOW_INTERRUPTIONS", default=False)
            serializer = CustomProtocolFrameSerializer(
                sample_rate=8000,
                channels=1,
                input_sample_rate=8000,
                output_sample_rate=8000,
            )
            # Vosk calls need a lower start_secs: single short answers like
            # "si" last ~200ms, and at start_secs=0.3 the VAD never confirms
            # speech start — no UserStartedSpeaking/Stopped frames, so Vosk's
            # VAD-stop finalize path (the only fast flush) never runs and the
            # answer is lost. 0.2s still filters line noise bursts.
            vad_start_secs = 0.2 if stt_provider == "vosk" else 0.3
            vad_stop_secs = 0.5 if allow_interruptions else 1.0
            transport = FastAPIWebsocketTransport(
                websocket=websocket_client,
                params=FastAPIWebsocketParams(
                    audio_out_enabled=True,
                    add_wav_header=False,
                    audio_in_sample_rate=8000,
                    audio_in_channels=1,
                    audio_in_enabled=True,
                    vad_analyzer=SileroVADAnalyzer(params=VADParams(
                        confidence=0.8,
                        start_secs=vad_start_secs,
                        stop_secs=vad_stop_secs,
                        min_volume=0.5)),
                    audio_in_passthrough=True,
                    serializer=serializer,
                    audio_out_sample_rate=8000,
                    audio_out_channels=1,
                ),
            )

            # 🔍 Transport配置详细调试日志
            logging.info(f"📡 Transport创建: socket={websocket_client}, stream_sid={stream_sid}")
            logging.info(f"📡 Transport音频配置: 输出采样率=8000Hz, 输出声道=1, WAV头=False")
            logging.info(
                f"📡 Transport VAD配置: 启用=True, 置信度=0.8, "
                f"开始={vad_start_secs}s, 停止={vad_stop_secs}s, 最小音量=0.5"
            )

            # 验证Transport参数
            if hasattr(transport, '_output') and hasattr(transport._output, '_params'):
                params = transport._output._params
                logging.info(
                    f"📡 Transport实际参数: audio_out_sample_rate={params.audio_out_sample_rate}Hz, audio_out_channels={params.audio_out_channels}")
            else:
                logging.warning("📡 无法获取Transport输出参数")

            # Create system prompt from configuration
            system_prompt_content = (
                llm_config.get("prompt", "") or websocket_prompt
            )
            system_prompt_content = create_system_prompt(system_prompt_content, tts_config)
            system_prompt = {
                "role": "system",
                "content": system_prompt_content
            }

            welcome_config = config_manager.get_welcome_config()
            welcome_text = prepare_text_for_tts(
                welcome_config.get("content") or "",
                tts_config,
                variables,
            )
            if not welcome_text:
                logging.warning("flowConfig.welcomeNode.content is empty; first response will not be triggered by LLM.")

            message_history = [system_prompt]

            # Official pipecat v0.0.104+ context + mute strategies.
            context = LLMContext(message_history)
            user_aggregator_params = LLMUserAggregatorParams(
                user_mute_strategies=[
                    FunctionCallUserMuteStrategy(),
                ] if allow_interruptions else [
                    AlwaysUserMuteStrategy(),
                    FunctionCallUserMuteStrategy(),
                ],
                user_turn_stop_timeout=2.0 if allow_interruptions else 5.0,
            )
            context_aggregator_pair = LLMContextAggregatorPair(
                context,
                user_params=user_aggregator_params,
            )
            user_context_aggregator, assistant_context_aggregator = context_aggregator_pair

            transcription_logger = TranscriptionLogger()

            @transcription_logger.event_handler("on_transcription")
            async def _on_transcription(logger, text, is_final):
                if not text:
                    return
                # Any valid user response resets silence follow-up count.
                session_context["no_response_count"] = 0
                session_context["last_transcription_at"] = time.monotonic()
                if not session_context.get("confirmation_grammar_active"):
                    return
                if _is_confirmation_phrase(text):
                    # Still inside the confirmation window: the customer may
                    # greet first ("hola"/"bueno") and only then answer the
                    # closed question ("si"/"no"). Releasing here would leave
                    # the real short answer to free decoding, where the small
                    # model often emits nothing at all. Keep the grammar armed
                    # and roll the release timer forward instead.
                    logging.info(
                        "Confirmation grammar kept: final %r matches grammar set", text
                    )
                    _rearm_grammar_release_timer()
                    return
                # Off-list free-form speech: the confirmation phase is over,
                # later turns need free decoding.
                await _release_confirmation_grammar("off-grammar transcript")

            # Get no response timeout configuration
            no_response_config = config_manager.get_no_response_config()
            timeout_sec = no_response_config.get("timeoutSec", 6)
            frame_logger = FrameLogger()
            context_timestamp_tracker = ContextMessageTimestampTracker(context)
            silence_detector = SilenceDetector(
                timeout_sec=timeout_sec,
                component="bot_beta",
            )
            interruption_state = {"assistant_turn_interrupted": False}

            async def _serialize_frame(frame):
                payload = serializer.serialize(frame)
                if asyncio.iscoroutine(payload):
                    payload = await payload
                return payload

            async def _queue_end_frame(reason: str):
                if session_context.get("is_end"):
                    logging.info(f"Skip EndFrame, already ended. reason={reason}")
                    return
                session_context["is_end"] = True
                hangup_state.is_hanging_up = True
                logging.info(f"Queue EndFrame. reason={reason}")
                if task:
                    await task.queue_frame(EndFrame())

            harden_pipecat_websocket_transport(
                transport,
                on_disconnect=partial(
                    _queue_end_frame,
                    "pipecat_output_send_disconnected",
                ),
                logger=logging.getLogger(__name__),
                description="bot_beta Pipecat websocket transport send",
            )

            async def _fallback_no_response_hangup():
                try:
                    # Leave enough time for the configured fixed farewell to
                    # finish playing before force-ending the call.
                    await asyncio.sleep(20)
                    if session_context.get("is_end"):
                        logging.info("Skip no-response fallback hangup, already ended.")
                        return
                    await send_hangup_signal(websocket_client)
                    await asyncio.sleep(0.5)
                    await _queue_end_frame("no_response_hangup")
                except asyncio.CancelledError:
                    logging.info("No-response fallback hangup task cancelled.")
                    raise
                except Exception as exc:
                    logging.error(f"No-response fallback hangup task error: {exc}")

            # Timeout monitor
            async def monitor_call_duration():
                while True:
                    await asyncio.sleep(1)
                    if time.time() - session_context["call_start_time"] >= call_timeout:
                        logging.info(f"Call duration limit ({call_timeout}s) reached.")
                        await send_hangup_signal(websocket_client)
                        await asyncio.sleep(2)
                        await _queue_end_frame("call_timeout")
                        break

            # Handle no response timeouts
            @silence_detector.event_handler("on_silence_detected")
            async def on_silence_detected(logger, silence_count):
                if session_context["is_end"]:
                    return
                if session_context.get("voicemail_detected"):
                    logging.info("Skip no-response follow-up, voicemail already detected.")
                    return
                if not session_context.get("on_answer"):
                    return
                if session_context.get("no_response_hangup_queued"):
                    logging.info(
                        "No-response hangup already queued, skip duplicate silence event."
                    )
                    return
                if hangup_state.is_hanging_up:
                    logging.info(
                        "Hangup already in progress (opening silence timeout), skip silence detection."
                    )
                    return

                local_no_response_config = config_manager.get_no_response_config()
                max_count = coerce_positive_int(
                    local_no_response_config.get("maxNoResponseCount", 3),
                    default_value=3,
                    field_name="maxNoResponseCount",
                )
                # Use session_context counter so that user responses (via _on_transcription)
                # actually reset the round — the detector's cumulative silence_count never resets.
                session_context["no_response_count"] += 1
                no_response_round = session_context["no_response_count"]
                logging.info(
                    "No-response detected: "
                    f"round={no_response_round}, max={max_count}, "
                    f"configured={local_no_response_config.get('maxNoResponseCount')!r}"
                )

                if no_response_round >= max_count:
                    hangup_config = local_no_response_config.get("noResponseHangUp") or {}
                    if hangup_config.get("enabled", True):
                        session_context["no_response_hangup_queued"] = True
                        hangup_message = hangup_config.get(
                            "hangupMessage",
                        )
                        fixed_hangup_text = prepare_text_for_tts(
                            hangup_message,
                            tts_config,
                            variables,
                        )
                        logging.info(
                            "Queue fixed no-response farewell TTS: "
                            f"round={no_response_round}, max={max_count}, "
                            f"text={fixed_hangup_text!r}"
                        )
                        if task and fixed_hangup_text:
                            preroll_frame = _build_silence_frame(WELCOME_AUDIO_PREROLL_MS)
                            if preroll_frame:
                                await task.queue_frame(preroll_frame)
                            await task.queue_frame(LLMFullResponseStartFrame())
                            await task.queue_frame(LLMTextFrame(fixed_hangup_text))
                            await task.queue_frame(LLMFullResponseEndFrame())
                        if not session_context.get("no_response_hangup_task"):
                            session_context["no_response_hangup_task"] = (
                                asyncio.create_task(_fallback_no_response_hangup())
                            )
                        return
                    else:
                        # enabled=False: 达到 max 后停止追问，但不主动挂断
                        logging.info(
                            "No-response max reached, hangup disabled, stopping follow-ups: "
                            f"round={no_response_round}, max={max_count}"
                        )
                        return

                no_response_reply = (
                    local_no_response_config.get("noResponseReply", "Hi, are you still here")
                    or "Hi, are you still here"
                )
                fixed_no_response_text = prepare_text_for_tts(
                    no_response_reply,
                    tts_config,
                    variables,
                )
                logging.info(
                    "Queue fixed no-response TTS: "
                    f"round={no_response_round}, max={max_count}, "
                    f"text={fixed_no_response_text!r}"
                )
                if task and fixed_no_response_text:
                    preroll_frame = _build_silence_frame(WELCOME_AUDIO_PREROLL_MS)
                    if preroll_frame:
                        await task.queue_frame(preroll_frame)
                    await task.queue_frame(LLMFullResponseStartFrame())
                    await task.queue_frame(LLMTextFrame(fixed_no_response_text))
                    await task.queue_frame(LLMFullResponseEndFrame())
                session_context["last_bot_speak_time"] = time.time()

            # Pipeline
            pipeline_params = PipelineParams(
                allow_interruptions=allow_interruptions,
                enable_metrics=True,
                enable_usage_metrics=True,
                report_only_initial_ttfb=False,
                audio_in_sample_rate=8000,
                audio_out_sample_rate=8000,
            )


            language = tts_config.get("locale", "zh-CN")

            async def _router_hangup_cancel():
                await _queue_end_frame("router_on_hangup")

            event_router = TelephonyEventRouter(cancel_task_cb=_router_hangup_cancel)

            async def _on_start(frame):
                logging.info(f"on_start: {frame.payload}")

            async def _on_ring(frame):
                logging.info(f"on_ring: {frame.payload}")

            async def _on_answer(frame):
                if session_context["on_answer"]:
                    logging.info(f"on_answer ignored duplicate: {frame.payload}")
                    return

                logging.info(f"on_answer: {frame.payload}")
                session_context["on_answer"] = True
                session_context["no_response_count"] = 0
                logging.info("trigger event: on_answer successfully")
                play_answer_prelude = False
                if play_answer_prelude:
                    audio_frame = sounds.get("incoming4.wav")
                    if audio_frame:
                        serialized_audio = await _serialize_frame(audio_frame)
                        if isinstance(serialized_audio, str):
                            sent = await safe_send_text(
                                websocket_client,
                                serialized_audio,
                                description="bot_beta incoming audio prelude (text)",
                                logger=logging.getLogger(__name__),
                                on_disconnect=partial(
                                    _queue_end_frame,
                                    "incoming_audio_send_disconnected",
                                ),
                            )
                            if not sent:
                                return
                        elif isinstance(serialized_audio, (bytes, bytearray)):
                            sent = await safe_send_bytes(
                                websocket_client,
                                bytes(serialized_audio),
                                description="bot_beta incoming audio prelude (bytes)",
                                logger=logging.getLogger(__name__),
                                on_disconnect=partial(
                                    _queue_end_frame,
                                    "incoming_audio_send_disconnected",
                                ),
                            )
                            if not sent:
                                return

                if task and welcome_text:
                    # Re-arm the flow watchdog: from here on we expect bot audio
                    # at the sink within its timeout, otherwise log an ERROR.
                    if flow_watchdog:
                        flow_watchdog.rearm()
                    preroll_frame = _build_silence_frame(WELCOME_AUDIO_PREROLL_MS)
                    if preroll_frame:
                        await task.queue_frame(preroll_frame)
                    logging.info(
                        "Queue fixed welcome TTS: preroll_ms=%d text=%r",
                        WELCOME_AUDIO_PREROLL_MS,
                        welcome_text,
                    )
                    await task.queue_frame(LLMFullResponseStartFrame())
                    await task.queue_frame(LLMTextFrame(welcome_text))
                    await task.queue_frame(LLMFullResponseEndFrame())
                    logging.info(
                        "Welcome frames queued: preroll + LLMFullResponseStart/Text/End "
                        "pushed to pipeline task queue"
                    )
                    if session_context.get("confirmation_grammar_active"):
                        # Rolling fallback release, anchored to the last user
                        # activity (welcome just played; nothing transcribed yet).
                        session_context["last_transcription_at"] = time.monotonic()
                        _rearm_grammar_release_timer()

                if shadow_decoder and not shadow_decoder.enabled:
                    # Connect the Deepgram shadow now that the call is answered.
                    # Best-effort: start() swallows its own errors.
                    await shadow_decoder.start()

            async def _on_dtmf(frame):
                logging.info(f"on_dtmf: {frame.payload}")

            async def _on_stat(frame):
                logging.debug(f"on_stat: {frame.payload}")

            async def _on_talk(frame):
                logging.debug(f"on_talk: {frame.payload}")

            async def _on_hangup(frame):
                logging.info(f"on_hangup: {frame.payload}")
                await _queue_end_frame("event_on_hangup")

            event_router.on("on_start", _on_start)
            event_router.on("on_ring", _on_ring)
            event_router.on("on_answer", _on_answer)
            event_router.on("on_dtmf", _on_dtmf)
            event_router.on("on_stat", _on_stat)
            event_router.on("on_talk", _on_talk)
            event_router.on("on_hangup", _on_hangup)

            if hasattr(transport, "event_handler"):
                @transport.event_handler("on_client_disconnected")
                async def on_client_disconnected(transport_obj, client):
                    logging.info(f"on_client_disconnected: {client}")
                    pipecat_client = getattr(transport_obj, "_client", None)
                    if pipecat_client:
                        pipecat_client._closing = True
                    await _queue_end_frame("client_disconnected")

                @transport.event_handler("on_session_timeout")
                async def on_session_timeout(transport_obj, client):
                    logging.info(f"on_session_timeout: {client}")
                    await _queue_end_frame("session_timeout")

            hangup_state = HangupState()

            async def _opening_silence_hangup_cb():
                hangup_state.is_hanging_up = True
                await send_hangup_signal(websocket_client)

            def _on_voicemail_detected():
                session_context["voicemail_detected"] = True
                session_context["no_response_count"] = 0
                hangup_state.input_blocked = True
                if fixed_voicemail_text:
                    hangup_state.block_ghost_llm = True
                logging.info("Voicemail detected; no-response follow-ups disabled.")
                if voicemail_mode != "hangup":
                    asyncio.create_task(_disconnect_stt_on_voicemail())

            async def _voicemail_hangup_on_detect():
                if hangup_state.is_hanging_up:
                    logging.info(
                        "Voicemail action hangup skipped, hangup already in progress."
                    )
                    return
                hangup_state.is_hanging_up = True
                logging.info("Voicemail detected and voicemailNode.action=hangup; hanging up now.")
                await send_hangup_signal(websocket_client)
                await _queue_end_frame("voicemail_action_hangup")

            async def _voicemail_fixed_message_done():
                if hangup_state.is_hanging_up:
                    logging.info(
                        "Fixed voicemail hangup skipped, hangup already in progress."
                    )
                    return
                existing_task = session_context.get("voicemail_hangup_task")
                if existing_task and not existing_task.done():
                    logging.info("Fixed voicemail hangup task already queued.")
                    return
                hangup_state.is_hanging_up = True

                async def _hangup_now():
                    try:
                        if session_context.get("is_end"):
                            logging.info("Skip fixed voicemail hangup, already ended.")
                            return
                        await send_hangup_signal(websocket_client)
                        await _queue_end_frame("fixed_voicemail_message_done")
                    except asyncio.CancelledError:
                        logging.info("Fixed voicemail hangup task cancelled.")
                        raise
                    except Exception as exc:
                        logging.error(f"Fixed voicemail hangup task error: {exc}")

                session_context["voicemail_hangup_task"] = asyncio.create_task(
                    _hangup_now()
                )

            async def _disconnect_stt_on_voicemail():
                await asyncio.sleep(0.5)
                try:
                    if hasattr(stt_service, "_disconnect"):
                        await stt_service._disconnect()
                        logging.info("Deepgram disconnected after voicemail detection.")
                except Exception as exc:
                    logging.warning(f"Failed to disconnect Deepgram after voicemail: {exc}")

            async def _finalize_stt_after_voicemail():
                try:
                    if not hasattr(stt_service, "request_finalize") or not hasattr(stt_service, "_connection"):
                        logging.info("Skip forced Deepgram finalize: stt_service has no finalize hooks.")
                        return
                    connection = stt_service._connection
                    if not connection or not await connection.is_connected():
                        logging.info("Skip forced Deepgram finalize: connection not ready.")
                        return
                    stt_service.request_finalize()
                    await connection.finalize()
                    logging.info("Forced Deepgram finalize after voicemail detection.")
                except Exception as exc:
                    logging.warning(f"Forced Deepgram finalize failed: {exc}")

            def _build_voicemail_remote_audio_config():
                return {
                    "bot_code": robot_code,
                    "provider": (tts_config.get("provider") or "cartesia").strip().lower(),
                    "voice_id": (
                        tts_config.get("voice_id")
                        or DEFAULT_CARTESIA_VOICE_ID
                    ),
                    "model": tts_config.get("model") or DEFAULT_MODEL,
                    "locale": tts_config.get("locale") or DEFAULT_LOCALE,
                    "sample_rate": AUDIO_ASSET_SAMPLE_RATE,
                    "audio_format": "wav",
                    "text_type": TEXT_TYPE_VOICEMAIL,
                    "live_text": fixed_voicemail_live_text,
                    "text": fixed_voicemail_text,
                    "full_text": voicemail_content,
                }

            def _build_voicemail_local_audio_config(asset: dict | None):
                if not asset:
                    return None
                return {
                    "bot_code": asset.get("bot_code"),
                    "provider": asset.get("provider"),
                    "voice_id": asset.get("voice_id"),
                    "model": asset.get("model"),
                    "locale": asset.get("locale"),
                    "sample_rate": asset.get("sample_rate"),
                    "audio_format": asset.get("audio_format"),
                    "text_type": asset.get("text_type"),
                    "text_hash": asset.get("text_hash"),
                    "text": asset.get("text"),
                    "file_path": asset.get("file_path"),
                    "status": asset.get("status"),
                    "enabled": asset.get("enabled"),
                    "fallback": asset.get("fallback"),
                }

            def _mark_voicemail_audio_playback(source: str, **kwargs):
                playback = {
                    "source": source,
                    "remote": _build_voicemail_remote_audio_config(),
                    **kwargs,
                }
                session_context["voicemail_audio_playback"] = playback
                return playback

            def _lookup_fixed_voicemail_audio():
                if not fixed_voicemail_cache_enabled:
                    _mark_voicemail_audio_playback(
                        "live_tts",
                        reason="cache_segment_missing",
                    )
                    return None

                remote_config = _build_voicemail_remote_audio_config()
                asset = find_audio_asset(robot_code)
                if not asset:
                    _mark_voicemail_audio_playback(
                        "live_tts",
                        reason="cache_miss",
                    )
                    logging.info(
                        "audio_asset_miss bot_code=%s reason=cache_miss",
                        robot_code,
                    )
                    return None

                expected = {
                    "provider": remote_config["provider"],
                    "voice_id": remote_config["voice_id"],
                    "model": remote_config["model"],
                    "locale": remote_config["locale"],
                    "sample_rate": remote_config["sample_rate"],
                    "audio_format": remote_config["audio_format"],
                    "text_type": remote_config["text_type"],
                    "text": normalize_audio_asset_text(remote_config["text"]),
                }
                mismatches = []
                for field, expected_value in expected.items():
                    actual_value = (
                        normalize_audio_asset_text(asset.get(field, ""))
                        if field == "text"
                        else asset.get(field)
                    )
                    if actual_value != expected_value:
                        mismatches.append(
                            f"{field}: expected={expected_value!r} actual={actual_value!r}"
                        )

                local_config = _build_voicemail_local_audio_config(asset)
                if mismatches:
                    _mark_voicemail_audio_playback(
                        "live_tts",
                        reason="cache_mismatch",
                        mismatches=mismatches,
                        local=local_config,
                    )
                    logging.info(
                        "audio_asset_miss bot_code=%s reason=cache_mismatch mismatches=%s",
                        robot_code,
                        mismatches,
                    )
                    return None

                try:
                    frame = load_audio_frame(asset)
                except Exception as exc:
                    _mark_voicemail_audio_playback(
                        "live_tts",
                        reason="cache_load_failed",
                        local=local_config,
                    )
                    logging.warning(
                        "audio_asset_miss bot_code=%s reason=cache_load_failed error=%s",
                        robot_code,
                        exc,
                    )
                    return None

                _mark_voicemail_audio_playback(
                    "local_cache",
                    local=local_config,
                )
                logging.info(
                    "audio_asset_hit bot_code=%s text_hash=%s file_path=%s",
                    robot_code,
                    asset.get("text_hash"),
                    asset.get("file_path"),
                )
                return {
                    "asset": asset,
                    "frame": frame,
                }

            # STTPreRollMuteProcessor gates mic audio during bot speech to cut
            # Deepgram billing. Local Vosk has no per-audio cost, and gating
            # starves it of the audio it needs for interim/final results (late
            # finals break interruption confirmation and leave the bot silent),
            # so bypass the mute for local STT providers.
            if stt_provider == "vosk":
                pre_stt_processors = []
            else:
                pre_stt_processors = [STTPreRollMuteProcessor()]

            pipeline_processors = [
                transport.input(),
                event_router,
                # Shadow tap: mirrors mic audio into the Deepgram shadow decoder
                # (observational only; forwards every frame unchanged).
                *([ShadowDecodeTap(shadow_decoder)] if shadow_decoder else []),
                *pre_stt_processors,
                stt_service,
                # Collects Vosk final transcripts for the end-of-call A/B row.
                *([vosk_final_collector] if vosk_final_collector else []),
                VoicemailDetector(
                    max_word_count=30,
                    language=language,
                    instant_close=voicemail_mode == "hangup",
                    instant_close_cb=(
                        _voicemail_hangup_on_detect
                        if voicemail_mode == "hangup"
                        else None
                    ),
                    detected_cb=_on_voicemail_detected,
                    finalize_stt_cb=_finalize_stt_after_voicemail,
                    fixed_voicemail_text=(
                        fixed_voicemail_text
                        if voicemail_mode == "fixed_message"
                        else None
                    ),
                    fixed_voicemail_live_text=(
                        fixed_voicemail_live_text
                        if voicemail_mode == "fixed_message"
                        else None
                    ),
                    fixed_voicemail_audio_lookup=(
                        _lookup_fixed_voicemail_audio
                        if voicemail_mode == "fixed_message"
                        else None
                    ),
                    fixed_voicemail_done_cb=(
                        _voicemail_fixed_message_done
                        if voicemail_mode == "fixed_message"
                        else None
                    ),
                ),
                transcription_logger,
                STTGatedInterruptor(),
                silence_detector,
                HangupGate(hangup_state),
                user_context_aggregator,
                HangupGate(hangup_state),
                ReplyProtectionInterruptionGate(interruption_state=interruption_state),
                llm_service,
                WordAwareSanitizer(
                    config_manager=config_manager,
                    transport=transport,
                    websocket_client=websocket_client,
                    hangup_state=hangup_state,
                    exit_delay=15
                ),
                tts_service,
                frame_logger,
                ContextSafeAssistantTurnGate(interruption_state=interruption_state),
                OpeningSilenceTimeoutProcessor(
                    hangup_cb=_opening_silence_hangup_cb,
                    opening_timeout=15.0,
                    post_stop_timeout=10.0,
                ),
                GracefulHangupController(
                    hangup_state=hangup_state,
                    hangup_cb=partial(send_hangup_signal, websocket_client),
                    fallback_exit_delay=25,
                    post_bot_stop_delay=0.2,
                    end_frame_delay=0.5,
                ),
                assistant_context_aggregator,
                context_timestamp_tracker,
                transport.output(),
            ]
            pipeline = Pipeline(pipeline_processors)

            # Official logger mode (ACTIVE): Pipecat observer.
            # task_observers = [
            #     DebugLogObserver(
            #         frame_types=(
            #             TranscriptionFrame,
            #             LLMTextFrame,
            #             BotStartedSpeakingFrame,
            #             BotStoppedSpeakingFrame,
            #         )
            #     )
            # ]
            # No observer mode / custom processor mode:
            flow_watchdog = PipelineFlowWatchdog(timeout_sec=10.0)
            task_observers = [CallMetricsObserver(metrics_collector), flow_watchdog]
            logging.info("Using official DebugLogObserver for frame diagnostics.")

            task = PipelineTask(
                pipeline,
                params=pipeline_params,
                observers=task_observers,
            )
            duration_monitor = asyncio.create_task(monitor_call_duration())

            # Run pipeline
            runner = PipelineRunner(handle_sigint=False)
            metrics_collector.task_start_time = datetime.datetime.now()
            await runner.run(task)
            if duration_monitor:
                duration_monitor.cancel()
            metrics_collector.finalize(datetime.datetime.now())
            logging.info('runner.run(task) done')

    except Exception as e:
        logging.error(traceback.format_exc())
        logging.error(f"Error in run_bot_beta: {e}")
        raise e
    finally:
        if duration_monitor and not duration_monitor.done():
            duration_monitor.cancel()
        if flow_watchdog:
            await flow_watchdog.cleanup()
        if session_context.get("silence_check_task"):
            session_context["silence_check_task"].cancel()
        no_response_hangup_task = session_context.get("no_response_hangup_task")
        if no_response_hangup_task and not no_response_hangup_task.done():
            no_response_hangup_task.cancel()
            try:
                await no_response_hangup_task
            except asyncio.CancelledError:
                pass
        voicemail_hangup_task = session_context.get("voicemail_hangup_task")
        if voicemail_hangup_task and not voicemail_hangup_task.done():
            voicemail_hangup_task.cancel()
            try:
                await voicemail_hangup_task
            except asyncio.CancelledError:
                pass

        services_to_stop = [
            ('TTS', tts_service),
            ('STT', stt_service),
            ('LLM', llm_service),
        ]
        
        for name, service in services_to_stop:
            if service and hasattr(service, 'stop'):
                try:
                    logging.info(f'Stopping {name} service...')
                    await asyncio.wait_for(
                        service.stop(EndFrame()),
                        timeout=3.0
                    )
                    logging.info(f'{name} service stopped')
                except asyncio.TimeoutError:
                    logging.warning(f'{name} service stop timeout')
                except Exception as e:
                    logging.warning(f'{name} service stop error: {e}')

        if shadow_decoder is not None:
            # Stop after STT so any late Vosk finals are already collected;
            # stop() drains queued audio so Deepgram's last finals arrive too.
            try:
                await asyncio.wait_for(shadow_decoder.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                logging.warning("[ShadowSTT] stop timeout (continuing)")
            except Exception as e:
                logging.warning(f"[ShadowSTT] stop error (ignored): {e}")
            try:
                vosk_text = vosk_final_collector.text if vosk_final_collector else ""
                deepgram_text = shadow_decoder.text
                if vosk_text or deepgram_text:
                    save_asr_shadow_compare(
                        call_uuid=call_uuid,
                        robot_code=robot_code,
                        language=(shadow_config or {}).get("language", ""),
                        vosk_model="vosk-model-small-es-0.42",
                        deepgram_model=(shadow_config or {}).get("model", ""),
                        vosk_text=vosk_text,
                        deepgram_text=deepgram_text,
                        vosk_segments=(
                            vosk_final_collector.final_segments
                            if vosk_final_collector
                            else []
                        ),
                        deepgram_segments=shadow_decoder.final_segments,
                    )
                else:
                    logging.info("[ShadowSTT] no transcripts on either side, skipping compare row")
            except Exception as e:
                logging.warning(f"[ShadowSTT] compare persist failed (ignored): {e}")

        logging.info('finally - bot_beta')
        await send_hangup_signal(websocket_client)

        if metrics_collector:
            metrics_collector.finalize(datetime.datetime.now())

        collection_note_result = None
        if context:
            await save_conversation(
                context,
                call_uuid=call_uuid,
                conversation_meta={
                    "user_name": variables.get("user_name", ""),
                    "business_id": business_id,
                    "remaining_amount": variables.get("remaining_amount", 0),
                    "app_type": app_type,
                    "country": country,
                    "voicemail_audio_playback": session_context.get("voicemail_audio_playback"),
                },
                call_data=call_data,
                metrics_collector=metrics_collector,
                intent_label_override="G" if session_context.get("voicemail_detected") else None,
            )
            if work_order_id:
                collection_note_result, task_type = await intelligent_notes_distribution(context, call_data, locale=tts_config.get("locale", "es-MX"))
                if collection_note_result:
                    metrics_collector.set_collection_note_result(collection_note_result, task_type)

        if metrics_collector:
            logging.info(f"call_metrics: {metrics_collector.to_dict()}")
            metrics_collector.save_to_db()
            await send_call_data_callback(
                context=context,
                metrics_collector=metrics_collector,
                collection_note=collection_note_result,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TerifAI Bot V3")
    parser.add_argument("--config", type=str, help="Bot configuration JSON")
    config = parser.parse_args()

    if config.config:
        call_data = json.loads(config.config)
        # Test the configuration parsing
        config_manager = BotV3ConfigManager(call_data)
        print("ASR Config:", config_manager.get_asr_config())
        print("TTS Config:", config_manager.get_tts_config())
        print("LLM Config:", config_manager.get_llm_config())
        print("Welcome Config:", config_manager.get_welcome_config())
