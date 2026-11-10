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
from dotenv import load_dotenv
from collection_agents.bots.helpers import build_call_context, build_session_state
from collection_agents.handlers.common.no_response import NoResponseController
from collection_agents.handlers.standard.voicemail import StandardVoicemailHandler
from collection_agents.bots.observers import PipelineFlowWatchdog
from collection_agents.bots.telephony import register_telephony_handlers
from collection_agents.bots.standard.grammar import _is_confirmation_phrase
from collection_agents.bots.standard.lifecycle import finalize_call as _finalize_call
from collection_agents.bots.standard.services import (
    create_call_services as _create_call_services,
)
from collection_agents.services.voicemail_audio_repository import VoicemailAudioRepository
from collection_agents.transport.telephony_websocket import (
    TelephonyFrameSerializer,
    TelephonyEventRouter,
)
from collection_agents.transport.websocket_safe_send import harden_pipecat_websocket_transport
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
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from collection_agents.handlers.standard.opening_silence import OpeningSilenceHandler
from collection_agents.processors.common.context import ContextMessageTimestampTracker
from collection_agents.processors.common.hangup import GracefulHangupController, HangupState
from collection_agents.processors.common.hangup_phrase_detector import HangupPhraseDetector
from collection_agents.processors.common.silence import SilenceDetector
from collection_agents.processors.common.voicemail_detector import VoicemailDetector
from collection_agents.processors.standard.hangup import HangupGate
from collection_agents.processors.standard.logging import FrameLogger, TranscriptionLogger

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    EndFrame,
)

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from collection_agents.metrics.collector import CallMetricsObserver
from collection_agents.services.call_config import (
    CallConfigManager,
    create_system_prompt,
    prepare_text_for_tts,
)
from collection_agents.transport.websocket_safe_send import send_hangup_signal
from collection_agents.services.stt_local import ShadowDecodeTap


load_dotenv(override=True)

WELCOME_AUDIO_PREROLL_MS = 500

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


def _build_user_aggregator_params(allow_interruptions: bool):
    return LLMUserAggregatorParams(
        user_turn_strategies=UserTurnStrategies(
            start=[
                MinWordsUserTurnStartStrategy(
                    min_words=1,
                    use_interim=True,
                )
            ],
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)],
        ),
        user_mute_strategies=(
            [FunctionCallUserMuteStrategy()]
            if allow_interruptions
            else [AlwaysUserMuteStrategy(), FunctionCallUserMuteStrategy()]
        ),
        user_turn_stop_timeout=2.0 if allow_interruptions else 5.0,
    )


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


async def run_standard_bot(websocket_client, stream_sid, call_data, call_timeout=180):
    """Run bot with v3 configuration system - fixes assistant message recording"""
    call_ctx = None
    svc = None
    session_context = {}
    context = None
    tts_config = {}
    task = None
    duration_monitor = None
    flow_watchdog = None
    metrics_collector = None
    llm_service = None
    # Deepgram shadow (A/B comparison vs live Vosk). Created only when the
    # robot runs Vosk AND shadow config resolves; kept in the outer scope so
    # the finally block can stop it and persist the comparison row.
    shadow_decoder = None
    vosk_final_collector = None

    try:
        call_ctx = build_call_context(call_data)
        config_manager = call_ctx["config_manager"]
        variables = call_ctx["variables"]
        voicemail_action = call_ctx["voicemail_action"]
        voicemail_hangup_on_detect = call_ctx["voicemail_hangup_on_detect"]
        robot_code = call_ctx["robot_code"]

        async with aiohttp.ClientSession() as session:
            session_context = build_session_state(
                websocket_client,
                stream_sid,
                config_manager,
                extra={
                    "last_user_stop_time": None,
                    "last_bot_speak_time": time.time(),
                    "silence_check_task": None,
                    "voicemail_hangup_task": None,
                    "voicemail_audio_playback": None,
                    "assistant_response_count": 0,
                },
            )

            logging.info(f"Call data config: {config_manager.config}")
            logging.info(
                "Voicemail node action=%r, hangup on detection: %s",
                voicemail_action,
                voicemail_hangup_on_detect,
            )

            # 服务创建收口在 _create_call_services（本文件上方），返回普通 dict 显式解包。
            svc = await _create_call_services(call_ctx, session_context, session, call_data)
            tts_config = svc["tts_config"]
            llm_config = svc["llm_config"]
            stt_provider = svc["stt_provider"]
            stt_service = svc["stt_service"]
            tts_service = svc["tts_service"]
            llm_service = svc["llm_service"]
            metrics_collector = svc["metrics_collector"]
            shadow_decoder = svc["shadow_decoder"]
            vosk_final_collector = svc["vosk_final_collector"]
            voicemail_mode = svc["voicemail_mode"]
            fixed_voicemail_text = svc["fixed_voicemail_text"]
            fixed_voicemail_cache_enabled = svc["fixed_voicemail_cache_enabled"]
            _release_confirmation_grammar = svc["release_confirmation_grammar"]
            _rearm_grammar_release_timer = svc["rearm_grammar_release_timer"]

            allow_interruptions = _get_env_bool("ALLOW_INTERRUPTIONS", default=True)
            serializer = TelephonyFrameSerializer(
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
                llm_config.get("prompt", "") or config_manager.get_prompt_config() or ""
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

            context = LLMContext(message_history)
            user_aggregator_params = _build_user_aggregator_params(allow_interruptions)
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
                component="standard",
            )

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
                description="standard Pipecat websocket transport send",
            )

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

            async def _queue_no_response_text(text, current_round, max_count):
                fixed_text = prepare_text_for_tts(text, tts_config, variables)
                if not task or not fixed_text:
                    return
                logging.info(
                    "Queue no-response TTS: round=%s max=%s text=%r",
                    current_round,
                    max_count,
                    fixed_text,
                )
                preroll_frame = _build_silence_frame(WELCOME_AUDIO_PREROLL_MS)
                if preroll_frame:
                    await task.queue_frame(preroll_frame)
                await task.queue_frame(LLMFullResponseStartFrame())
                await task.queue_frame(LLMTextFrame(fixed_text))
                await task.queue_frame(LLMFullResponseEndFrame())

            async def _on_no_response_reply(text, current_round, max_count):
                await _queue_no_response_text(text, current_round, max_count)
                session_context["last_bot_speak_time"] = time.time()

            async def _on_no_response_hangup():
                await send_hangup_signal(websocket_client)
                await asyncio.sleep(0.5)
                await _queue_end_frame("no_response_hangup")

            no_response_controller = NoResponseController(
                session_context=session_context,
                config_manager=config_manager,
                is_hanging_up=lambda: hangup_state.is_hanging_up,
                on_reply=_on_no_response_reply,
                on_farewell=_queue_no_response_text,
                on_hangup=_on_no_response_hangup,
            )
            no_response_controller.register(silence_detector)

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

            async def _on_answer(frame):
                if session_context["on_answer"]:
                    logging.info(f"on_answer ignored duplicate: {frame.payload}")
                    return

                logging.info(f"on_answer: {frame.payload}")
                session_context["on_answer"] = True
                session_context["no_response_count"] = 0
                logging.info("trigger event: on_answer successfully")

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

            register_telephony_handlers(
                event_router,
                transport,
                on_answer=_on_answer,
                on_end=_queue_end_frame,
            )

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


            voicemail_audio_repository = VoicemailAudioRepository(
                robot_code=robot_code,
                tts_config=tts_config,
                text=fixed_voicemail_text,
                cache_enabled=fixed_voicemail_cache_enabled,
                on_playback=lambda playback: session_context.update(
                    voicemail_audio_playback=playback
                ),
            )

            pipeline_processors = [
                transport.input(),
                event_router,
                # Shadow tap: mirrors mic audio into the Deepgram shadow decoder
                # (observational only; forwards every frame unchanged).
                *([ShadowDecodeTap(shadow_decoder)] if shadow_decoder else []),
                stt_service,
                # Collects Vosk final transcripts for the end-of-call A/B row.
                *([vosk_final_collector] if vosk_final_collector else []),
                VoicemailDetector(
                    max_word_count=30,
                ),
                StandardVoicemailHandler(
                    mode=voicemail_mode,
                    locale=language,
                    on_detected=_on_voicemail_detected,
                    finalize_stt_cb=_finalize_stt_after_voicemail,
                    instant_close_cb=_voicemail_hangup_on_detect,
                    fixed_text=(
                        fixed_voicemail_text
                        if voicemail_mode == "fixed_message"
                        else None
                    ),
                    fixed_audio_lookup=voicemail_audio_repository.find_cached_audio,
                    fixed_done_cb=_voicemail_fixed_message_done,
                ),
                transcription_logger,
                silence_detector,
                HangupGate(hangup_state),
                user_context_aggregator,
                HangupGate(hangup_state),
                llm_service,
                HangupPhraseDetector(
                    config_manager=config_manager,
                    transport=transport,
                    websocket_client=websocket_client,
                    hangup_state=hangup_state,
                    exit_delay=15
                ),
                tts_service,
                frame_logger,
                OpeningSilenceHandler(
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
            flow_watchdog = PipelineFlowWatchdog(
                sink=transport.output(),
                timeout_sec=10.0,
            )
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
        logging.error(f"Error in run_standard_bot: {e}")
        raise e
    finally:
        await _finalize_call(
            call_ctx, svc, session_context, context, duration_monitor, flow_watchdog
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TerifAI Bot V3")
    parser.add_argument("--config", type=str, help="Bot configuration JSON")
    config = parser.parse_args()

    if config.config:
        call_data = json.loads(config.config)
        # Test the configuration parsing
        config_manager = CallConfigManager(call_data)
        print("ASR Config:", config_manager.get_asr_config())
        print("TTS Config:", config_manager.get_tts_config())
        print("LLM Config:", config_manager.get_llm_config())
        print("Welcome Config:", config_manager.get_welcome_config())
