import argparse
import asyncio
import aiohttp
import datetime
import hashlib
import json
import logging
import time
import traceback
from functools import partial
from typing import Optional

from dotenv import load_dotenv
from pipecat import __version__
from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
from pipecat.frames.frames import (
    EndFrame,
    LLMRunFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.openai.realtime.events import (
    AudioConfiguration,
    AudioInput,
    AudioOutput,
    ConversationItemInputAudioTranscriptionCompleted,
    InputAudioNoiseReduction,
    InputAudioTranscription,
    PCMAudioFormat,
    SessionProperties,
    TurnDetection,
)
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from collection_agents.session_lifecycle import SessionFastAPIWebsocketTransport
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_mute import (
    FunctionCallUserMuteStrategy,
)

from collection_agents.services.call_config import (
    CallConfigManager,
    build_first_question_content,
    coerce_positive_int,
    create_system_prompt,
    normalize_language_code,
)
from collection_agents.bots.helpers import build_call_context, build_session_state
from collection_agents.handlers.common.no_response import NoResponseController
from collection_agents.handlers.realtime.voicemail import RealtimeVoicemailHandler
from collection_agents.bots.telephony import register_telephony_handlers
from collection_agents.bots.realtime.azure_service import (
    AzureRealtimeResponseCoordinator,
)
from collection_agents.bots.realtime.transcript_observer import RealtimeTranscriptObserver
from collection_agents.bots.realtime.metrics import RealtimeCallMetricsObserver
from collection_agents.services.realtime_prompts import (
    build_exact_say_prompt as _build_realtime_exact_say_prompt,
    build_no_response_hangup_prompt as _build_realtime_no_response_hangup_prompt,
)
from collection_agents.bots.realtime.resources import (
    get_realtime_resource_manager as _get_realtime_resource_manager,
)
from collection_agents.bots.realtime.utils import _is_effective_realtime_transcript
from collection_agents.services.realtime_context import summarize_for_log as _summarize_for_log
from collection_agents.notes.call_notes import persist_call_artifacts
from collection_agents.metrics.collector import CallMetricsCollector
from collection_agents.metrics.collector import RealtimeMetricsCollector
from collection_agents.transport.telephony_websocket import (
    TelephonyFrameSerializer,
    TelephonyEventRouter,
)
from collection_agents.transport.websocket_safe_send import (
    harden_pipecat_websocket_transport,
    send_hangup_signal,
)
from collection_agents.processors.common.context import ContextMessageTimestampTracker
from collection_agents.processors.common.hangup import GracefulHangupController, HangupState
from collection_agents.processors.common.hangup_phrase_detector import HangupPhraseDetector
from collection_agents.processors.common.silence import SilenceDetector
from collection_agents.processors.common.voicemail_detector import VoicemailDetector
from collection_agents.processors.realtime.hangup import RealtimeHangupGate
from collection_agents.processors.realtime.speaking_state import RealtimeBotSpeakingStateTracker
from collection_agents.processors.realtime.transcription_monitor import RealtimeInputTranscriptionMonitor



REALTIME_INPUT_SAMPLE_RATE = 24000
TELEPHONY_SAMPLE_RATE = 8000
AZURE_REALTIME_VOICE_POOL = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
)

def _preserve_realtime_transcription_usage():
    try:
        ConversationItemInputAudioTranscriptionCompleted.model_config = {
            **ConversationItemInputAudioTranscriptionCompleted.model_config,
            "extra": "allow",
        }
        ConversationItemInputAudioTranscriptionCompleted.model_rebuild(force=True)
    except Exception as exc:
        logging.warning("Failed to preserve Realtime transcription usage: %s", exc)


_preserve_realtime_transcription_usage()


def _coerce_bool_flag(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _select_realtime_voice(call_uuid: str = "", stream_sid: str = "") -> str:
    seed = (call_uuid or stream_sid or str(time.time())).strip()
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % len(AZURE_REALTIME_VOICE_POOL)
    return AZURE_REALTIME_VOICE_POOL[index]


def _build_realtime_session_properties(
    *,
    system_prompt_content: str,
    asr_config: dict,
    tts_config: dict,
    llm_config: dict,
    realtime_voice: str,
    transcription_model: str,
    allow_interruptions: bool,
) -> SessionProperties:
    locale = asr_config.get("locale") or tts_config.get("locale", "zh-CN")
    language = normalize_language_code(locale, default="zh")
    default_vad_threshold = 0.78
    default_silence_duration_ms = 450
    max_output_tokens = coerce_positive_int(
        llm_config.get("max_completion_tokens", 1024),
        default_value=1024,
        field_name="max_completion_tokens",
    )
    max_output_tokens = max(max_output_tokens, 1024)

    return SessionProperties(
        output_modalities=["audio"],
        instructions=system_prompt_content,
        max_output_tokens=max_output_tokens,
        audio=AudioConfiguration(
            input=AudioInput(
                format=PCMAudioFormat(),
                transcription=InputAudioTranscription(
                    model=transcription_model,
                    language=language,
                ),
                noise_reduction=InputAudioNoiseReduction(type="far_field"),
                turn_detection=TurnDetection(
                    threshold=default_vad_threshold,
                    prefix_padding_ms=300,
                    silence_duration_ms=default_silence_duration_ms,
                ),
            ),
            output=AudioOutput(
                format=PCMAudioFormat(),
                voice=realtime_voice,
            ),
        ),
    )


print(f"pipecat version: {__version__}")

load_dotenv(override=True)



def _build_rnnoise_filter(enabled: bool = False):
    if not enabled:
        logging.info("Azure Realtime RNNoise input filter disabled.")
        return None
    try:
        logging.info("Azure Realtime RNNoise input filter enabled: quality=QQ")
        return RNNoiseFilter(resampler_quality="QQ")
    except Exception as exc:
        logging.warning("Azure Realtime RNNoise input filter unavailable: %s", exc)
        return None


async def _setup_realtime_call(call_ctx, session_context, websocket_client, stream_sid, realtime_rnnoise_enabled):
    """Realtime 通话设置：配置解析、Azure 资源获取、指标收集器、序列化器与传输层。

    从 run_realtime_bot 抽出的设置段；返回普通 dict，主函数显式解包。
    失败时释放已获取的 realtime 资源，避免泄漏。
    """
    config_manager = call_ctx["config_manager"]
    robot_code = call_ctx["robot_code"]
    robot_version_id = call_ctx["robot_version_id"]
    call_uuid = call_ctx["call_uuid"]

    realtime_resource = None
    try:
        asr_config = config_manager.get_asr_config()
        tts_config = config_manager.get_tts_config()
        llm_config = config_manager.get_llm_config()
        llm_metrics_config = dict(llm_config)

        realtime_resource = await _get_realtime_resource_manager().acquire()
        realtime_url = realtime_resource["base_url"]
        realtime_model = realtime_resource["model"]
        realtime_asr_model = realtime_resource["asr_model"]
        realtime_voice = _select_realtime_voice(call_uuid, stream_sid)

        metrics_collector = RealtimeMetricsCollector(
            call_uuid=call_uuid,
            robot_code=robot_code,
            robot_version_id=robot_version_id,
            bot_mode="realtime",
            task_start_time=datetime.datetime.now(),
            asr_info={
                **asr_config,
                "provider": "azure_realtime",
                "model": realtime_asr_model,
                "sample_rate": REALTIME_INPUT_SAMPLE_RATE,
            },
            llm_info={
                **llm_metrics_config,
                "provider": "azure_realtime",
                "model": realtime_model,
                "base_url": realtime_url,
            },
            tts_info={
                **tts_config,
                "provider": "azure_realtime",
                "model": realtime_model,
                "voice_id": realtime_voice,
                "sample_rate": REALTIME_INPUT_SAMPLE_RATE,
            },
        )
        metrics_collector.llm_model_name = realtime_model

        allow_interruptions = False
        logging.info(
            "Realtime alpha interruptions: allow_interruptions=%s",
            allow_interruptions,
        )
        realtime_monitor_state = {
            "bot_speaking": False,
            "assistant_response_active": False,
            "barge_in_item_ids": set(),
            "input_transcription_events": [],
        }
        session_context["realtime_monitor_state"] = realtime_monitor_state
        serializer = TelephonyFrameSerializer(
            sample_rate=REALTIME_INPUT_SAMPLE_RATE,
            channels=1,
            input_sample_rate=TELEPHONY_SAMPLE_RATE,
            output_sample_rate=TELEPHONY_SAMPLE_RATE,
        )
        transport_class = (
            SessionFastAPIWebsocketTransport
            if session_lifecycle is not None else FastAPIWebsocketTransport
        )
        transport = transport_class(
            websocket=websocket_client,
            params=FastAPIWebsocketParams(
                audio_out_enabled=True,
                add_wav_header=False,
                audio_in_sample_rate=REALTIME_INPUT_SAMPLE_RATE,
                audio_in_channels=1,
                audio_in_enabled=True,
                audio_in_filter=_build_rnnoise_filter(realtime_rnnoise_enabled),
                # Azure Realtime uses 24 kHz PCM. Pipecat's Silero VAD
                # only supports 8 kHz or 16 kHz, so turn detection is
                # delegated to Azure server_vad in SessionProperties.
                vad_analyzer=None,
                audio_in_passthrough=True,
                serializer=serializer,
                audio_out_sample_rate=TELEPHONY_SAMPLE_RATE,
                audio_out_channels=1,
            ),
        )

    except Exception:
        if realtime_resource:
            await _get_realtime_resource_manager().release(realtime_resource)
        raise

    return {
        "asr_config": asr_config,
        "tts_config": tts_config,
        "llm_config": llm_config,
        "realtime_resource": realtime_resource,
        "realtime_url": realtime_url,
        "realtime_model": realtime_model,
        "realtime_asr_model": realtime_asr_model,
        "realtime_voice": realtime_voice,
        "metrics_collector": metrics_collector,
        "realtime_monitor_state": realtime_monitor_state,
        "serializer": serializer,
        "transport": transport,
        "allow_interruptions": allow_interruptions,
    }


async def _finalize_realtime_call(call_ctx, svc, session_context, context, duration_monitor, llm_service):
    """Realtime 通话收尾：停监控/服务、释放资源、会话与纪要落库、指标落库、挂机信令。

    从 run_realtime_bot 的 finally 段抽出；svc / call_ctx 允许为 None。
    """
    call_ctx = call_ctx or {}
    svc = svc or {}
    websocket_client = session_context.get("websocket_client")
    metrics_collector = svc.get("metrics_collector")
    realtime_resource = svc.get("realtime_resource")

    if duration_monitor and not duration_monitor.done():
        duration_monitor.cancel()
    no_response_hangup_task = session_context.get("no_response_hangup_task")
    if no_response_hangup_task and not no_response_hangup_task.done():
        no_response_hangup_task.cancel()
        try:
            await no_response_hangup_task
        except asyncio.CancelledError:
            pass

    if llm_service and hasattr(llm_service, "stop"):
        try:
            logging.info("Stopping Azure realtime service...")
            await asyncio.wait_for(llm_service.stop(EndFrame()), timeout=3.0)
            logging.info("Azure realtime service stopped")
        except asyncio.TimeoutError:
            logging.warning("Azure realtime service stop timeout")
        except Exception as exc:
            logging.warning(f"Azure realtime service stop error: {exc}")

    if realtime_resource:
        await _get_realtime_resource_manager().release(realtime_resource)

    logging.info("finally - realtime")
    await send_hangup_signal(websocket_client)

    if metrics_collector:
        metrics_collector.finalize(datetime.datetime.now())

    if metrics_collector:
        logging.info(f"call_metrics: {metrics_collector.to_dict()}")
        metrics_collector.save_to_db()

    await persist_call_artifacts(
        context=context,
        call_data=call_ctx.get("call_data") or {},
        call_uuid=call_ctx.get("call_uuid", ""),
        locale=(svc.get("tts_config") or {}).get("locale", ""),
        metrics_collector=metrics_collector,
    )


async def run_realtime_bot(
    websocket_client,
    stream_sid,
    call_data,
    call_timeout=180,
    session_lifecycle=None,
):
    """Run the collection bot using Azure GA Realtime via Pipecat."""
    call_ctx = None
    svc = None
    session_context = {}
    context = None
    tts_config = {}
    task = None
    duration_monitor = None
    llm_service = None
    realtime_resource = None
    metrics_collector: Optional[CallMetricsCollector] = None

    try:
        call_ctx = build_call_context(call_data)
        config_manager = call_ctx["config_manager"]
        variables = call_ctx["variables"]
        voicemail_action = call_ctx["voicemail_action"]
        voicemail_instant_close = call_ctx["voicemail_hangup_on_detect"]
        realtime_rnnoise_enabled = _coerce_bool_flag(
            variables.get("realtime_rnnoise_enabled", 0),
            default=False,
        )

        async with aiohttp.ClientSession():
            session_context = build_session_state(websocket_client, stream_sid, config_manager)

            logging.info(f"Realtime alpha call data config: {config_manager.config}")
            logging.info(
                "Realtime alpha voicemail node action=%r, instant close enabled: %s",
                voicemail_action,
                voicemail_instant_close,
            )
            logging.info(
                "Realtime alpha RNNoise enabled: %s",
                realtime_rnnoise_enabled,
            )

            # 通话设置收口在 _setup_realtime_call（本文件上方），返回普通 dict 显式解包。
            svc = await _setup_realtime_call(
                call_ctx, session_context, websocket_client, stream_sid, realtime_rnnoise_enabled
            )
            asr_config = svc["asr_config"]
            tts_config = svc["tts_config"]
            llm_config = svc["llm_config"]
            realtime_resource = svc["realtime_resource"]
            realtime_url = svc["realtime_url"]
            realtime_model = svc["realtime_model"]
            realtime_asr_model = svc["realtime_asr_model"]
            realtime_voice = svc["realtime_voice"]
            metrics_collector = svc["metrics_collector"]
            realtime_monitor_state = svc["realtime_monitor_state"]
            serializer = svc["serializer"]
            transport = svc["transport"]
            allow_interruptions = svc["allow_interruptions"]

            # Create system prompt from configuration. Keep this identical to
            # standard.py because realtime receives the same call_data.
            system_prompt_content = (
                llm_config.get("prompt", "") or config_manager.get_prompt_config() or ""
            )
            system_prompt_content = create_system_prompt(system_prompt_content, tts_config, variables)
            system_prompt = {
                "role": "system",
                "content": system_prompt_content,
            }

            first_question = {
                "role": "user",
                "content": build_first_question_content(tts_config.get("locale", "zh-CN"))
            }

            message_history = [system_prompt, first_question]
            context = LLMContext(message_history)

            session_properties = _build_realtime_session_properties(
                system_prompt_content=system_prompt_content,
                asr_config=asr_config,
                tts_config=tts_config,
                llm_config=llm_config,
                realtime_voice=realtime_voice,
                transcription_model=realtime_asr_model,
                allow_interruptions=allow_interruptions,
            )
            llm_service = AzureRealtimeResponseCoordinator(
                api_key=realtime_resource["api_key"],
                base_url=realtime_url,
                model=realtime_model,
                session_properties=session_properties,
                start_audio_paused=True,
                allow_interruptions=allow_interruptions,
                realtime_voice=realtime_voice,
                metrics_collector=metrics_collector,
                realtime_monitor_state=realtime_monitor_state,
            )

            user_aggregator_params = LLMUserAggregatorParams(
                user_turn_strategies=UserTurnStrategies(
                    start=[
                        TranscriptionUserTurnStartStrategy(
                            use_interim=False,
                            enable_interruptions=False,
                        )
                    ],
                    stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.8)],
                ),
                user_mute_strategies=[
                    FunctionCallUserMuteStrategy(),
                ],
            )
            context_aggregator_pair = LLMContextAggregatorPair(
                context,
                user_params=user_aggregator_params,
            )
            user_context_aggregator, assistant_context_aggregator = (
                context_aggregator_pair
            )

            no_response_config = config_manager.get_no_response_config()
            timeout_sec = no_response_config.get("timeoutSec", 6)
            silence_detector = SilenceDetector(
                timeout_sec=timeout_sec,
                component="realtime",
            )
            context_timestamp_tracker = ContextMessageTimestampTracker(context)

            def _mark_realtime_user_activity_from_transcript(
                text: str,
                source: str,
            ):
                if not _is_effective_realtime_transcript(text):
                    return
                silence_detector.mark_user_activity()
                logging.info(
                    "Realtime silence timer reset from ASR transcript: "
                    "source=%s text=%r",
                    source,
                    _summarize_for_log(text),
                )

            async def _on_transcription(text):
                if text:
                    session_context["no_response_count"] = 0
                    _mark_realtime_user_activity_from_transcript(
                        text,
                        "final_transcription",
                    )

            transcript_context_persister = RealtimeTranscriptObserver(
                context,
                on_transcription=_on_transcription,
            )

            async def _serialize_frame(frame):
                payload = serializer.serialize(frame)
                if asyncio.iscoroutine(payload):
                    payload = await payload
                return payload

            async def _delete_realtime_conversation_item(item_id: str):
                if not llm_service or not item_id:
                    return
                await llm_service.delete_conversation_item_once(item_id)

            async def _on_confirmed_barge_in(event: dict):
                text = (event or {}).get("text", "").strip()
                if not text:
                    return False
                session_context["no_response_count"] = 0
                _mark_realtime_user_activity_from_transcript(
                    text,
                    "confirmed_barge_in",
                )
                await llm_service.cancel_current_response_for_barge_in(event)
                await llm_service.append_user_message_and_create_response(
                    context,
                    text,
                    item_id=(event or {}).get("item_id", ""),
                )
                return True

            async def _on_partial_barge_in(event: dict):
                text = (event or {}).get("text", "").strip()
                if not text:
                    return False
                return await llm_service.cancel_current_response_for_barge_in(event)

            async def _queue_end_frame(reason: str):
                if session_context.get("is_end"):
                    logging.info(f"Skip EndFrame, already ended. reason={reason}")
                    return
                session_context["is_end"] = True
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
                description="realtime Pipecat websocket transport send",
            )


            async def monitor_call_duration():
                while True:
                    await asyncio.sleep(1)
                    if time.time() - session_context["call_start_time"] >= call_timeout:
                        logging.info(f"Call duration limit ({call_timeout}s) reached.")
                        await send_hangup_signal(websocket_client)
                        await asyncio.sleep(2)
                        await _queue_end_frame("call_timeout")
                        break


            async def _send_no_response_prompt(prompt):
                if llm_service:
                    await llm_service.append_context_message_and_create_response(
                        context,
                        prompt,
                    )
                else:
                    context.add_message(prompt)

            async def _on_no_response_reply(message, current_round, max_count):
                await _send_no_response_prompt({
                    "role": "system",
                    "content": _build_realtime_exact_say_prompt(
                        tts_config.get("locale", "es-MX"),
                        message,
                    ),
                })

            async def _on_no_response_farewell(message, current_round, max_count):
                await _send_no_response_prompt({
                    "role": "system",
                    "content": _build_realtime_no_response_hangup_prompt(
                        tts_config.get("locale", "es-MX"),
                        message or "hi, are you still here?",
                    ),
                })

            async def _on_no_response_hangup():
                await send_hangup_signal(websocket_client)
                await asyncio.sleep(0.5)
                await _queue_end_frame("no_response_hangup")

            no_response_controller = NoResponseController(
                session_context=session_context,
                config_manager=config_manager,
                is_hanging_up=lambda: hangup_state.is_hanging_up,
                on_reply=_on_no_response_reply,
                on_farewell=_on_no_response_farewell,
                on_hangup=_on_no_response_hangup,
            )
            no_response_controller.register(silence_detector)

            pipeline_params = PipelineParams(
                allow_interruptions=True,
                enable_metrics=True,
                enable_usage_metrics=True,
                report_only_initial_ttfb=False,
                audio_in_sample_rate=REALTIME_INPUT_SAMPLE_RATE,
                audio_out_sample_rate=TELEPHONY_SAMPLE_RATE,
            )

            async def _router_hangup_cancel():
                await _queue_end_frame("router_on_hangup")

            event_router = TelephonyEventRouter(cancel_task_cb=_router_hangup_cancel)

            async def _on_answer(frame):
                if session_context["on_answer"]:
                    logging.info(f"on_answer ignored duplicate: {frame.payload}")
                    return

                logging.info(f"on_answer: {frame.payload}")
                session_context["on_answer"] = True
                session_context["no_response_count"] = 0


                if task:
                    await task.queue_frame(LLMRunFrame())

            register_telephony_handlers(
                event_router,
                transport,
                on_answer=_on_answer,
                on_end=_queue_end_frame,
            )

            hangup_state = HangupState()

            realtime_voicemail_handler = RealtimeVoicemailHandler(
                session_context=session_context,
                context=context,
                hangup_state=hangup_state,
                llm_service=llm_service,
                locale=tts_config.get("locale", "es-MX"),
                instant_close=voicemail_instant_close,
                on_hangup=partial(send_hangup_signal, websocket_client),
                on_end=_queue_end_frame,
            )

            # Realtime pipeline: keep only the currently tested processors active.
            # Add the commented diagnostics/gates back one by one after the
            # voicemail trigger path is confirmed stable.
            pipeline_processors = [
                transport.input(),
                event_router,
                user_context_aggregator,
                VoicemailDetector(
                    max_word_count=256,
                    mode="realtime",
                    on_detected=realtime_voicemail_handler.handle,
                ),
                transcript_context_persister,
                silence_detector,
                RealtimeHangupGate(hangup_state),
                RealtimeInputTranscriptionMonitor(
                    realtime_monitor_state,
                    delete_item_cb=_delete_realtime_conversation_item,
                    voice_assistant_cb=realtime_voicemail_handler.handle,
                    partial_barge_in_cb=_on_partial_barge_in,
                    confirmed_barge_in_cb=_on_confirmed_barge_in,
                ),
                llm_service,
                RealtimeBotSpeakingStateTracker(
                    realtime_monitor_state,
                    on_bot_stopped_speaking_cb=llm_service._drain_pending_forced_responses,
                ),
                HangupPhraseDetector(
                    config_manager=config_manager,
                    transport=transport,
                    websocket_client=websocket_client,
                    hangup_state=hangup_state,
                    mode="realtime",
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

            task = PipelineTask(
                pipeline,
                params=pipeline_params,
                observers=[RealtimeCallMetricsObserver(metrics_collector)],
            )
            duration_monitor = asyncio.create_task(monitor_call_duration())

            runner = PipelineRunner(handle_sigint=False)
            metrics_collector.task_start_time = datetime.datetime.now()
            logging.info(
                "Starting realtime alpha: resource=%s base_url=%s model=%s voice=%s",
                realtime_resource["id"] if realtime_resource else "",
                realtime_url,
                realtime_model,
                realtime_voice,
            )
            if session_lifecycle is not None:
                await session_lifecycle.ready()
            await runner.run(task)
            if session_lifecycle is not None:
                await session_lifecycle.completed({
                    "voicemail": bool(session_context.get("voicemail_detected"))
                })
            if duration_monitor:
                duration_monitor.cancel()
            metrics_collector.finalize(datetime.datetime.now())
            logging.info("runner.run(task) done - realtime")

    except Exception as exc:
        logging.error(traceback.format_exc())
        logging.error(f"Error in run_realtime_bot: {exc}")
        raise
    finally:
        await _finalize_realtime_call(
            call_ctx, svc, session_context, context, duration_monitor, llm_service
        )
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TerifAI Realtime Alpha Bot")
    parser.add_argument("--config", type=str, help="Bot configuration JSON")
    config = parser.parse_args()

    if config.config:
        call_data = json.loads(config.config)
        config_manager = CallConfigManager(call_data)
        print("ASR Config:", config_manager.get_asr_config())
        print("TTS Config:", config_manager.get_tts_config())
        print("LLM Config:", config_manager.get_llm_config())
