"""
AI Copilot Bot - Complete Pipeline Implementation
Implements the CollectionAgent & VoiceAgent interaction protocol
"""
import asyncio
import datetime
import inspect
import json
import logging
import os
import sys
import time
import traceback
import uuid
from functools import partial
from typing import Any
from loguru import logger as loguru_logger
from dotenv import load_dotenv
from collection_agents.bots.service_metrics import (
    track_stt_reconnects,
    track_tts_reconnects,
)
from collection_agents.bots.copilot.config import BotCopilotConfigManager
from collection_agents.bots.copilot.protocol import CopilotProtocolClient
from collection_agents.handlers.copilot.hangup import CopilotWordAwareHangupProcessor
from collection_agents.processors.copilot.conditional import ConditionalProcessor
from collection_agents.processors.copilot.vad import AICopilotVADProcessor
from collection_agents.bots.copilot.services import (
    create_llm_service,
    create_stt_service,
    create_tts_service,
)
from collection_agents.transport.websocket_safe_send import harden_pipecat_websocket_transport

# Third-party imports
from openai.types.chat import ChatCompletionToolParam
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    LLMRunFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

# Local imports
from collection_agents.transport.fastapi_websocket_ai_copilot_beta import (
    AICopilotEventObserver,
    AICopilotEventRouter,
    AICopilotFrameSerializer,
    InnerEventRouter,
    Role,
    create_hangup_event,
    create_heartbeat_event,
    create_text_event,
)
from collection_agents.services.call_config import build_first_question_content, build_no_response_hangup_prompt
from collection_agents.services.providers import attach_llm_retry_counter
from collection_agents.processors.common.silence import SilenceDetector
from collection_agents.metrics.collector import CallMetricsCollector, CallMetricsObserver

load_dotenv(override=True)

NO_RESPONSE_TIMEOUT_SEC_FLOOR = 24
NO_RESPONSE_MAX_COUNT_CEILING = 1
COPILOT_HANGUP_EXIT_DELAY_SEC = 15.0
COPILOT_END_FRAME_DELAY_SEC = 4.0


# def format_prompt_with_variables(template: str, variables: dict) -> str:
#     """Format prompt template with variables"""
#     result = template
#     for key, value in variables.items():
#         placeholder = f"{{{{{key}}}}}"
#         result = result.replace(placeholder, str(value))
#     return result
async def run_copilot_bot_beta(websocket, event: dict[str, Any]):
    """
    Run AI Copilot bot with complete Pipeline

    Args:
        websocket: FastAPI WebSocket connection
        session_create: Session creation data with configuration
    """
    # Extract session identifiers

    # Extract session_create data
    session_create = event.get("session_create", {}) or {}
    session_json = json.dumps(session_create, ensure_ascii=False)
    logging.info(f"Received session_create: {session_json}")

    call_uuid = event.get("call_uuid") or session_create.get("callUUID") or str(uuid.uuid4())
    task_uuid = event.get("task_uuid") or session_create.get("taskUUID") or ""
    business_id = event.get("business_id") or session_create.get("businessID") or ""

    logging.info(
        f"Starting Copilot bot - call_uuid: {call_uuid}, "
        f"task_uuid: {task_uuid}, business_id: {business_id}"
    )

    # Parse configuration
    config_manager = BotCopilotConfigManager(session_create)
    config = session_create.get("config", {}) or {}
    variables = config.get("variables", {}) or {}
    if not isinstance(variables, dict):
        variables = {}
    robot_code = config.get("code", "")
    robot_version_id = config.get("robotVersionId") or config.get("robot_version_id")

    # Session state
    session_context = {
        "is_answered": False,
        "is_ended": False,
        "is_ending": False,
        "is_hangup_prompt_queued": False,
        "manual_intervention": False,  # Manual intervention mode
        "current_speaker_role": "CUSTOMER",  # Track current speaker
        "start_time": time.time(),
    }

    # Initialize variables before try block to avoid NameError in finally
    heartbeat_task = None
    context = None
    message_history = []
    stt_service = None
    tts_service = None
    llm_service = None
    asr_config = {}
    tts_config = {}
    llm_config = {}
    metrics_collector = None

    try:
        # Setup services
        asr_config = config_manager.get_asr_config()
        tts_config = config_manager.get_tts_config()
        llm_config = config_manager.get_llm_config()

        logging.info(f"ASR Config: {asr_config}")
        logging.info(f"TTS Config: {tts_config}")
        logging.info(f"LLM Config: {llm_config}")

        system_prompt_content = config_manager.get_prompt_config()
        metrics_collector = CallMetricsCollector(
            call_uuid=call_uuid,
            robot_code=robot_code,
            robot_version_id=robot_version_id,
            bot_mode="copilot",
            task_start_time=datetime.datetime.now(),
            asr_info={**asr_config, "sample_rate": 8000},
            llm_info=llm_config,
            tts_info={**tts_config, "sample_rate": 8000},
        )

        # Create services
        stt_service = create_stt_service(asr_config)
        tts_service = create_tts_service(tts_config)
        llm_service = create_llm_service(llm_config)
        attach_llm_retry_counter(llm_service, metrics_collector)

        track_tts_reconnects(tts_service, metrics_collector)
        track_stt_reconnects(stt_service, metrics_collector)

        system_prompt = {
            "role": "system",
            "content": system_prompt_content
        }

        # Initialize message history
        message_history = [system_prompt]

        # Create LLM context
        tools = [
            ChatCompletionToolParam(
                type="function",
                function={
                    "name": "end_the_conversation",
                    "description": (
                        "Call this method to end the conversation "
                        "when the goal is achieved or conversation "
                        "should end."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                }
            )
        ]

        tools_schema = ToolsSchema(
            standard_tools=[],
            custom_tools={AdapterType.SHIM: tools},
        )
        context = LLMContext(messages=list(message_history), tools=tools_schema)
        user_turn_strategies = UserTurnStrategies(
            start=[TranscriptionUserTurnStartStrategy(use_interim=False)],
            stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)],
        )
        # Keep user input unmuted to ensure transcriptions always reach LLM.
        user_aggregator_params = LLMUserAggregatorParams(
            user_turn_strategies=user_turn_strategies,
            user_mute_strategies=[],
        )
        context_aggregator_pair = LLMContextAggregatorPair(
            context,
            user_params=user_aggregator_params,
        )
        user_context_aggregator, assistant_context_aggregator = context_aggregator_pair

        serializer = AICopilotFrameSerializer(
            call_uuid=call_uuid,
            task_uuid=task_uuid,
            business_id=business_id,
            input_sample_rate=8000,
            output_sample_rate=8000,
            channels=1,
        )

        copilot_vad_params = VADParams(
            confidence=0.7,
            start_secs=0.3,
            stop_secs=0.6,
            min_volume=0.55,
        )
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                serializer=serializer,
                audio_in_enabled=True,
                audio_in_sample_rate=8000,
                audio_in_channels=1,
                audio_out_enabled=True,
                audio_out_sample_rate=8000,
                audio_out_channels=1,
                add_wav_header=False,
                vad_enabled=True,
                vad_analyzer=SileroVADAnalyzer(
                    params=copilot_vad_params,
                ),
                vad_audio_passthrough=True,
            ),
        )
        copilot_vad = AICopilotVADProcessor(
            SileroVADAnalyzer(sample_rate=8000, params=copilot_vad_params),
            name="copilot_vad",
        )

        logging.info(
            f"Transport created: call_uuid={call_uuid}, "
            f"task_uuid={task_uuid}, business_id={business_id}"
        )

        # Initialized after event_router is created.
        transcription_observer = None
        llm_text_observer = None

        # No-response detector (independent from frame logging)
        no_response_config = config_manager.get_flow_config().get(
            "noResponse",
            {}
        )
        configured_timeout_sec = no_response_config.get("timeoutSec", NO_RESPONSE_TIMEOUT_SEC_FLOOR)
        timeout_sec = max(configured_timeout_sec, NO_RESPONSE_TIMEOUT_SEC_FLOOR)
        silence_detector = SilenceDetector(
            timeout_sec=timeout_sec,
            component="bot_copilot_beta",
        )

        task = None

        async def queue_end_frame(reason: str):
            caller = inspect.stack()[1]
            caller_name = caller.function
            caller_file = os.path.basename(caller.filename)
            caller_line = caller.lineno
            if session_context["is_ended"]:
                logging.info(
                    "Skip EndFrame, already ended. "
                    f"reason={reason}, caller={caller_name} ({caller_file}:{caller_line})"
                )
                return
            session_context["is_ending"] = True
            session_context["is_ended"] = True
            logging.info(
                "Queue EndFrame. "
                f"reason={reason}, caller={caller_name} ({caller_file}:{caller_line})"
            )
            if task:
                await task.queue_frame(EndFrame())

        harden_pipecat_websocket_transport(
            transport,
            on_disconnect=partial(
                queue_end_frame,
                "pipecat_output_send_disconnected",
            ),
            logger=logging.getLogger(__name__),
            description="bot_copilot_beta Pipecat websocket transport send",
        )

        protocol_client = CopilotProtocolClient(
            transport=transport,
            websocket=websocket,
            call_uuid=call_uuid,
            task_uuid=task_uuid,
            business_id=business_id,
            on_disconnect=partial(queue_end_frame, "protocol_send_disconnected"),
        )
        send_text_event = protocol_client.send_text
        send_heartbeat_event = protocol_client.send_heartbeat
        send_hangup_event = protocol_client.send_hangup

        async def router_hangup_cancel():
            await queue_end_frame("router_on_hangup")





        async def graceful_hangup_sequence(
            reason: str,
            *,
            exit_delay: float = COPILOT_HANGUP_EXIT_DELAY_SEC,
        ):
            if session_context["is_ended"]:
                return
            session_context["is_ending"] = True
            await asyncio.sleep(exit_delay)
            if session_context["is_ended"]:
                return
            await send_hangup_event()
            await asyncio.sleep(COPILOT_END_FRAME_DELAY_SEC)
            await queue_end_frame(reason)

        event_router = AICopilotEventRouter(
            cancel_task_cb=router_hangup_cancel,
            forward_customer_text=True,
        )
        inner_event_router = InnerEventRouter()
        transcription_observer = AICopilotEventObserver(
            inner_event_router,
            emit_transcription=True,
        )
        llm_text_observer = AICopilotEventObserver(
            inner_event_router,
            emit_llm_text=True,
        )

        async def on_session_create(frame):
            logging.info(f"Session created from event stream: {frame.payload}")

        async def on_answer(frame):
            """Handle answer event - call was answered."""
            if session_context["is_answered"]:
                return
            logging.info("Call answered")
            session_context["is_answered"] = True

            welcome_message = {
                "role": "user",
                "content": build_first_question_content(tts_config.get("locale", "zh-CN")),
            }
            message_history.append(welcome_message)
            if task:
                context.add_message(welcome_message)
                await task.queue_frame(LLMRunFrame())

        async def on_audio_received(frame):
            """Handle audio received - update current speaker role."""
            role = frame.role or Role.CUSTOMER
            session_context["current_speaker_role"] = role

        async def on_manual_intervention(frame):
            """
            Handle manual intervention - disable LLM/TTS and keep transcription.
            """
            logging.info("Manual intervention started - AI suspended")
            session_context["manual_intervention"] = True

        async def on_customer_text(frame):
            """Handle customer text input (testing and fallback channel)."""
            text = frame.text or ""
            if not text:
                return
            logging.info(f"Customer text: {text}")
            session_context["current_speaker_role"] = frame.role or Role.CUSTOMER

            if session_context["manual_intervention"]:
                # Prevent forwarding to LLM path while manual intervention is active.
                frame.text = ""
                await send_text_event(
                    f"[Transcription] {text}",
                    role=Role.ROBOT,
                    source="manual_intervention_transcription",
                )
                return
            # No manual LLM trigger here. The router forwards text as
            # TranscriptionFrame and aggregator strategies trigger LLM.

        async def on_heartbeat(frame):
            """Handle heartbeat - echo back."""
            await send_heartbeat_event()

        async def on_hangup(frame):
            """Handle hangup from peer."""
            logging.info(f"Call hangup detected: {frame.payload}")
            await queue_end_frame("event_on_hangup")

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport_obj, client):
            logging.info(f"on_client_disconnected: {client}")
            pipecat_client = getattr(transport_obj, "_client", None)
            if pipecat_client:
                pipecat_client._closing = True
            await queue_end_frame("client_disconnected")

        @transport.event_handler("on_session_timeout")
        async def on_session_timeout(transport_obj, client):
            logging.info(f"on_session_timeout: {client}")
            await queue_end_frame("session_timeout")

        async def on_transcription(frame):
            """Handle transcription routed by custom event router."""
            text = frame.text or ""
            is_final = bool(frame.payload.get("is_final", True))
            if not is_final or not text:
                return

            # Always send ASR result as text event with correct role
            # (per protocol specification)
            speaker_role = session_context["current_speaker_role"]
            if speaker_role not in (Role.CUSTOMER, Role.AGENT, Role.ROBOT, Role.SYSTEM):
                speaker_role = Role.CUSTOMER
            sent = await send_text_event(
                text,
                role=speaker_role,
                source="asr_transcription",
            )
            if not sent:
                return
            logging.debug(
                "Transcription forwarded: "
                f"role={speaker_role}, text={text}, is_final={is_final}, "
                f"manual_intervention={session_context['manual_intervention']}, "
                f"is_ended={session_context['is_ended']}"
            )
            # No manual LLM trigger here. User context aggregation and
            # turn-stop strategies are responsible for LLM triggering.

        async def on_llm_text(frame):
            """Handle LLM generated text routed by custom event router."""
            text = frame.text or ""
            if not text:
                return

            # Send LLM output as text event with ROBOT role
            sent = await send_text_event(
                text,
                role=Role.ROBOT,
                source="llm",
            )
            if not sent:
                return

        async def on_router_silence_detected(frame):
            """Handle no response timeout routed via custom event router."""
            silence_count = int(frame.payload.get("silence_count", 0))
            if (
                session_context["is_ended"]
                or session_context["is_ending"]
                or session_context["is_hangup_prompt_queued"]
            ):
                return

            if session_context["manual_intervention"]:
                # Don't send prompts in manual intervention mode
                return

            configured_max_count = no_response_config.get("maxNoResponseCount", NO_RESPONSE_MAX_COUNT_CEILING)
            max_count = min(configured_max_count, NO_RESPONSE_MAX_COUNT_CEILING)

            if silence_count >= max_count:
                # Hang up after max silence
                hangup_config = no_response_config.get(
                    "noResponseHangUp",
                    {}
                )
                if hangup_config.get("enabled", True):
                    hangup_message = hangup_config.get(
                        "hangupMessage",
                        "感谢你的接听，让我们下次再联系，再见"
                    )
                    final_prompt = {
                        "role": "system",
                        "content": build_no_response_hangup_prompt(
                            tts_config.get("locale", "es-MX"),
                            hangup_message,
                        ),
                    }
                    message_history.append(final_prompt)
                    context.add_message(final_prompt)
                    session_context["is_hangup_prompt_queued"] = True
                    await task.queue_frame(LLMRunFrame())
                    async def _fallback_no_response_hangup():
                        await asyncio.sleep(35)
                        if not session_context["is_ended"]:
                            await graceful_hangup_sequence(
                                "no_response_hangup_fallback",
                                exit_delay=0.0,
                            )

                    asyncio.create_task(_fallback_no_response_hangup())
                    return

            # Send no response prompt
            no_response_reply = no_response_config.get(
                "noResponseReply",
                "请问您还在吗？"
            )
            continue_prompt = {
                "role": "system",
                "content": f"请说：{no_response_reply}"
            }
            message_history.append(continue_prompt)
            context.add_message(continue_prompt)
            await task.queue_frame(LLMRunFrame())

        # Handle silence detection and re-emit through the custom router.
        @silence_detector.event_handler("on_silence_detected")
        async def on_silence_detected(logger, silence_count):
            await inner_event_router.emit(
                "on_silence_detected",
                payload={"silence_count": silence_count},
            )

        @user_context_aggregator.event_handler("on_user_turn_started")
        async def on_user_turn_started(aggregator, strategy, params=None):
            logging.info(
                "User turn started: "
                f"strategy={strategy}, "
                f"params={params}, "
                f"manual_intervention={session_context['manual_intervention']}, "
                f"is_ended={session_context['is_ended']}"
            )

        @user_context_aggregator.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(aggregator, strategy, params=None):
            logging.info(
                "User turn stopped: "
                f"strategy={strategy}, "
                f"params={params}, "
                f"manual_intervention={session_context['manual_intervention']}, "
                f"is_ended={session_context['is_ended']}"
            )

        @user_context_aggregator.event_handler("on_user_turn_stop_timeout")
        async def on_user_turn_stop_timeout(aggregator):
            logging.warning(
                "User turn stop timeout: "
                f"manual_intervention={session_context['manual_intervention']}, "
                f"is_ended={session_context['is_ended']}"
            )

        event_router.on("on_session_create", on_session_create)
        event_router.on("on_answer", on_answer)
        event_router.on("on_audio_received", on_audio_received)
        event_router.on("on_manual_intervention", on_manual_intervention)
        event_router.on("on_customer_text", on_customer_text)
        event_router.on("on_heartbeat", on_heartbeat)
        event_router.on("on_hangup", on_hangup)

        inner_event_router.on("on_transcription", on_transcription)
        inner_event_router.on("on_llm_text", on_llm_text)
        inner_event_router.on("on_silence_detected", on_router_silence_detected)

        # Build pipeline with conditional processors
        # Create condition function
        def should_skip_llm():
            """Returns True when new LLM runs should be skipped."""
            return session_context["manual_intervention"] or session_context["is_ending"]

        def should_skip_tts():
            """Returns True when TTS should be skipped."""
            return session_context["manual_intervention"]


        # Create conditional processors (gate keepers)
        llm_gate = ConditionalProcessor(
            condition_func=should_skip_llm,
            name="llm_gate"
        )

        tts_gate = ConditionalProcessor(
            condition_func=should_skip_tts,
            name="tts_gate"
        )
        hangup_sanitizer = CopilotWordAwareHangupProcessor(
            on_hangup_requested=partial(
                graceful_hangup_sequence,
                "end_phrase_hangup",
            ),
            on_hangup_started=partial(
                session_context.__setitem__,
                "is_ending",
                True,
            ),
            name="copilot_hangup_sanitizer",
        )

        # Unified pipeline with conditional gates
        # When manual_intervention=True, frames are dropped at gates
        # When manual_intervention=False, frames pass through normally
        pipeline = Pipeline([
            transport.input(),
            event_router,
            copilot_vad,
            stt_service,
            transcription_observer,
            silence_detector,
            user_context_aggregator,
            llm_gate,                    # Gate before LLM
            llm_service,
            llm_text_observer,           # Capture LLM output text
            hangup_sanitizer,
            tts_gate,                    # Gate before TTS
            tts_service,
            transport.output(),
            assistant_context_aggregator,
        ])

        # Pipeline parameters
        pipeline_params = PipelineParams(
            allow_interruptions=False,
            enable_metrics=True,
            enable_usage_metrics=True,
            report_only_initial_ttfb=False,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        )

        # Create task
        task = PipelineTask(
            pipeline,
            params=pipeline_params,
            observers=[CallMetricsObserver(metrics_collector)],
        )

        async def heartbeat_loop():
            """Send heartbeat to peer periodically."""
            logging.info("Sending heartbeat every 6 seconds")
            while not session_context["is_ended"]:
                await asyncio.sleep(6)
                if session_context["is_ended"]:
                    break
                try:
                    sent = await send_heartbeat_event()
                    if not sent:
                        break
                except Exception as e:
                    logging.error(f"Heartbeat error: {e}")
                    break

        heartbeat_task = asyncio.create_task(heartbeat_loop())

        # Run pipeline
        runner = PipelineRunner(handle_sigint=False)
        metrics_collector.task_start_time = datetime.datetime.now()
        await runner.run(task)
        metrics_collector.finalize(datetime.datetime.now())

        logging.info("Pipeline completed")

    except asyncio.CancelledError:
        # Handle task cancellation (e.g., when WebSocket is forcibly closed)
        logging.warning("Copilot bot task was cancelled (WebSocket closed)")
        # Don't re-raise, let finally block execute
    except Exception as e:
        logging.error(f"Error in copilot bot: {e} with call_uuid: {call_uuid}")
        logging.error(traceback.format_exc())

    finally:
        # Cleanup - ensure heartbeat task is properly cancelled
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                logging.debug("Heartbeat task cancelled successfully")
            except Exception as e:
                logging.error(f"Error cancelling heartbeat task: {e}")
        
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
        
        if metrics_collector is not None:
            metrics_collector.finalize(datetime.datetime.now())

        session_context["is_ended"] = True

        if metrics_collector is not None:
            logging.info(f"copilot call_metrics: {metrics_collector.to_dict()}")
            metrics_collector.save_to_db()

        logging.info("Copilot bot session ended")


if __name__ == "__main__":
    # Test configuration parsing
    test_session_create = {
        "callUUID": "test-uuid-123",
        "taskUUID": "task-456",
        "businessID": "biz-789",
        "config": {
            "asr": {
                "provider": "deepgram",
                "locale": "zh-CN",
                "name": "中文"
            },
            "tts": {
                "provider": "cartesia",
                "name": "voice-id-123",
                "model": "sonic-2",
                "locale": "zh-CN"
            },
            "modelConfig": {
                "provider": "openrouter",
                "name": "openai/gpt-4o",
                "config": {
                    "temperature": 0.2,
                    "maxTokens": 100
                }
            }
        }
    }

    config_manager = BotCopilotConfigManager(test_session_create)
    print("ASR Config:", config_manager.get_asr_config())
    print("TTS Config:", config_manager.get_tts_config())
    print("LLM Config:", config_manager.get_llm_config())
