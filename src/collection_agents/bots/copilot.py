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
import re
import sys
import time
import traceback
import uuid
from functools import partial
from types import MethodType
from typing import Any
from loguru import logger as loguru_logger
from collection_agents.text.time_utils import utc2tz
from dotenv import load_dotenv
from collection_agents.transport.websocket_safe_send import (
    harden_pipecat_websocket_transport,
    safe_send_ws_message,
)

# Third-party imports
from openai.types.chat import ChatCompletionToolParam
from pipecat.adapters.schemas.tools_schema import AdapterType, ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMRunFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
    SystemFrame,
    TextFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.azure.llm import AzureLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService, GenerationConfig
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from deepgram import LiveOptions

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
from collection_agents.storage.db import MysqlClient
from collection_agents.services.call_config import build_first_question_content, build_no_response_hangup_prompt
from collection_agents.services.providers import attach_llm_retry_counter, select_azure_llm_target
from collection_agents.processors.common import SilenceDetector
from collection_agents.metrics.collector import CallMetricsCollector, CallMetricsObserver
from collection_agents.notes.call_notes import intelligent_notes_distribution

load_dotenv(override=True)
from collection_agents.infra.env import mode



NO_RESPONSE_TIMEOUT_SEC_FLOOR = 24
NO_RESPONSE_MAX_COUNT_CEILING = 1
COPILOT_HANGUP_EXIT_DELAY_SEC = 15.0
COPILOT_END_FRAME_DELAY_SEC = 4.0

# LLM defaults (can be overridden by modelConfig.config when provided).
LLM_TIMEOUT_SECS_DEFAULT = 6.0
LLM_MAX_RETRIES_DEFAULT = 3
LLM_RETRY_ON_TIMEOUT_DEFAULT = True

# nova-3/nova-2 分流名单统一由 bot_beta_modules.services.resolve_deepgram_model
# 从 conf/routing.json 读取，此处不再维护硬编码名单。
from collection_agents.services.providers import resolve_deepgram_model


def _coerce_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_deepgram_asr_model(robot_code: str) -> str:
    return resolve_deepgram_model(robot_code)


class ConditionalProcessor(FrameProcessor):
    """
    Conditional processor that gates frame flow based on a condition function.
    When condition returns True, frames are dropped (not passed downstream).
    When condition returns False, frames pass through normally.

    Note: System frames (StartFrame, EndFrame, etc.) always pass through.
    """

    def __init__(self, condition_func, **kwargs):
        super().__init__(**kwargs)
        self._condition_func = condition_func

    async def process_frame(self, frame, direction):
        """Process frame based on condition"""
        # CRITICAL: Always call parent's process_frame first
        # This ensures system frames (StartFrame, etc.) are properly handled
        # and internal state (__started, __process_queue) is initialized
        await super().process_frame(frame, direction)

        # Always let system frames pass through
        system_frames = (
            StartFrame | EndFrame | CancelFrame | SystemFrame
        )
        if isinstance(frame, system_frames):
            await self.push_frame(frame, direction)
            return

        # Check condition for non-system frames
        if self._condition_func():
            # Condition is True: drop the frame (don't pass downstream)
            logging.debug(
                f"{self.name}: Dropping frame {frame.__class__.__name__}"
            )
            return

        # Condition is False: pass frame through normally
        await self.push_frame(frame, direction)


class AICopilotVADProcessor(FrameProcessor):
    """Runs VAD after the copilot protocol router has materialized audio frames."""

    def __init__(self, vad_analyzer, **kwargs):
        super().__init__(**kwargs)
        self._vad_analyzer = vad_analyzer
        self._vad_state = VADState.QUIET

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            sample_rate = getattr(frame, "audio_in_sample_rate", None) or 8000
            self._vad_analyzer.set_sample_rate(sample_rate)
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            if not self._vad_analyzer.sample_rate:
                self._vad_analyzer.set_sample_rate(frame.sample_rate or 8000)

            new_vad_state = await self._vad_analyzer.analyze_audio(frame.audio)
            if (
                new_vad_state != self._vad_state
                and new_vad_state not in (VADState.STARTING, VADState.STOPPING)
            ):
                if new_vad_state == VADState.SPEAKING:
                    await self.push_frame(
                        VADUserStartedSpeakingFrame(
                            start_secs=self._vad_analyzer.params.start_secs,
                        ),
                        direction,
                    )
                elif new_vad_state == VADState.QUIET:
                    await self.push_frame(
                        VADUserStoppedSpeakingFrame(
                            stop_secs=self._vad_analyzer.params.stop_secs,
                        ),
                        direction,
                    )
                self._vad_state = new_vad_state

            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


class CopilotWordAwareHangupProcessor(FrameProcessor):
    """Starts the hangup flow only after the model has produced an ending phrase."""

    def __init__(self, *, on_hangup_requested, on_hangup_started=None, **kwargs):
        super().__init__(**kwargs)
        self._on_hangup_requested = on_hangup_requested
        self._on_hangup_started = on_hangup_started
        self._buffer = ""
        self._is_hanging_up = False
        self._is_ended = False
        self._endcall_regex = re.compile(
            r"(?i)("
            r"\b(?:goodbye|bye|bye-bye|see you|have a great day|have a good one|"
            r"adi[oó]s|hasta\s+(?:luego|mañana|pronto)|nos\s+vemos|chao|chau|cu[ií]date|"
            r"paalam|hanggang\s+sa\s+muli|ingat\s+ka|"
            r"que\s+tenga\s+(?:un\s+)?(?:lindo|buen|excelente)\s+d[ií]a)\b|"
            r"再见|拜拜|挂了|"
            r"\[\[HANGUP\]\]"
            r")"
        )

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if self._is_hanging_up:
            if isinstance(frame, (TextFrame, InterruptionFrame)):
                return
            if isinstance(frame, EndFrame):
                self._is_ended = True
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            self._buffer += frame.text
            await self.push_frame(frame, direction)
            self._maybe_start_hangup()
            return

        if isinstance(frame, (LLMFullResponseEndFrame, EndFrame)):
            self._maybe_start_hangup()
            self._buffer = ""
            if isinstance(frame, EndFrame):
                self._is_ended = True
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _maybe_start_hangup(self):
        clean_text = self._buffer.strip()
        if not clean_text or self._is_hanging_up:
            return
        if self._endcall_regex.search(clean_text):
            logging.info(f"Detected copilot end phrase, start graceful hangup: {clean_text[:120]!r}")
            self._is_hanging_up = True
            if self._on_hangup_started:
                self._on_hangup_started()
            self.create_task(self._perform_hangup(), "copilot_graceful_hangup")

    async def _perform_hangup(self):
        try:
            await self._on_hangup_requested()
        except Exception as e:
            logging.error(f"Copilot graceful hangup error: {e}")


class BotCopilotConfigManager:
    """Manages bot configuration from session_create data"""

    def __init__(self, session_create: dict[str, Any]):
        self.config = session_create.get("config", {})

    def get_asr_config(self):
        """Get ASR configuration"""
        asr = self.config.get("asr", {})
        return {
            "code": asr.get("code", ""),
            "provider": asr.get("provider", "deepgram"),
            "locale": asr.get("locale", "zh-CN"),
            "name": asr.get("name", "中文"),
            "model": asr.get("model", "nova-2"),
        }

    def get_tts_config(self):
        """Get TTS configuration"""
        tts = self.config.get("tts", {})
        ttsConfig = self.config.get("ttsConfig", {})
        tts = tts if isinstance(tts, dict) else {}
        ttsConfig = ttsConfig if isinstance(ttsConfig, dict) else {}
        return {
            "provider": tts.get("provider", "cartesia"),
            "voice_id": tts.get("name", ""),
            "model": tts.get("model", "sonic-3.6"),
            "locale": tts.get("locale", "zh-CN"),
            "volume": ttsConfig.get("volume", 1.0),
            "speed": ttsConfig.get("rate", 0.9),
        }

    def get_llm_config(self):
        """Get LLM configuration"""
        model_config = self.config.get("modelConfig", {})
        config = model_config.get("config", {}) if isinstance(model_config.get("config"), dict) else {}
        provider = model_config.get("provider", "openrouter")

        if provider == "openrouter":
            default_base_url = "https://openrouter.ai/api/v1"
            default_api_key = os.getenv("OPENROUTER_API_KEY")
        elif provider == "azure":
            default_base_url = ""
            default_api_key = ""
        else:
            default_base_url = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1")
            default_api_key = os.getenv("OPENAI_API_KEY")

        llm_timeout_secs = config.get("llmTimeoutSecs", LLM_TIMEOUT_SECS_DEFAULT)
        max_retries = config.get("maxRetries", LLM_MAX_RETRIES_DEFAULT)
        retry_on_timeout = config.get("retryOnTimeout", LLM_RETRY_ON_TIMEOUT_DEFAULT)
        max_completion_tokens = config.get("maxTokens", 256)
        temperature = config.get("temperature", 0.2)
        top_p = config.get("topP", 0.9)
        frequency_penalty = config.get("frequencyPenalty", 0)
        presence_penalty = config.get("presencePenalty", 0)
        seed = config.get("seed", 42)

        return {
            "provider": provider,
            "model": model_config.get("name", "openai/gpt-4o"),
            "base_url": model_config.get("url") or default_base_url,
            "api_key": model_config.get("apiKey") or default_api_key,
            "temperature": temperature,
            "top_p": top_p,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
            "seed": seed,
            "max_completion_tokens": max_completion_tokens,
            "llm_timeout_secs": llm_timeout_secs,
            "max_retries": max_retries,
            "retry_on_timeout": retry_on_timeout,
        }

    def get_prompt_config(self):
        """Get prompt configuration"""
        prompt_config = self.config.get("promptConfig", {})
        return prompt_config.get("content", "")

    def get_flow_config(self):
        """Get flow configuration"""
        return self.config.get("flowConfig", {})


def create_stt_service(asr_config):
    """Create STT service based on configuration"""
    provider = asr_config.get("provider", "deepgram")
    locale = asr_config.get("locale", "zh-CN")
    model = asr_config.get("model", "nova-2")

    if provider == "deepgram":
        return DeepgramSTTService(
            api_key=os.environ.get("DEEPGRAM_API_KEY"),
            live_options=LiveOptions(
                encoding="linear16",
                language=locale,
                model=model,
                sample_rate=8000,
                channels=1,
                interim_results=True,
                smart_format=True,
            )
        )
    else:
        logging.warning(
            f"Unknown STT provider: {provider}, using deepgram"
        )
        return create_stt_service({
            "provider": "deepgram",
            "locale": locale
        })


def create_tts_service(tts_config):
    """Create TTS service based on configuration"""
    provider = tts_config.get("provider", "cartesia")
    voice_id = tts_config.get("voice_id", "")
    model = tts_config.get("model", "sonic-3.6")
    locale = tts_config.get("locale", "zh-CN")

    if provider == "cartesia":
        language = locale.split("-")[0]
        generation_config_kwargs = {}
        volume = tts_config.get("volume")
        speed = tts_config.get("speed")
        if volume is not None:
            # backend [0, 100] → cartesia [0.5, 2.0]
            generation_config_kwargs["volume"] = round(0.5 + float(volume) / 100.0 * 1.5, 4)
        if speed is not None:
            # backend [0.5, 2] → cartesia [0.6, 1.5]
            generation_config_kwargs["speed"] = round(0.6 + (float(speed) - 0.5) / 1.5 * 0.9, 4)
        generation_config = (
            GenerationConfig(**generation_config_kwargs)
            if generation_config_kwargs
            else None
        )
        params = CartesiaTTSService.InputParams(
            language=language,
            generation_config=generation_config,
        )
        default_voice = "e50405f9-347e-4a1d-b5b6-943bbdb30cd5"
        model_name = (
            model if model.startswith("sonic")
            else "sonic-multilingual"
        )
        return CartesiaTTSService(
            api_key=os.getenv("CARTESIA_API_KEY"),
            voice_id=voice_id or default_voice,
            model=model_name,
            params=params,
            sample_rate=8000,
        )
    else:
        logging.warning(
            f"Unknown TTS provider: {provider}, using cartesia"
        )
        return create_tts_service({
            "provider": "cartesia",
            "voice_id": voice_id,
            "model": model,
            "locale": locale,
            "volume": tts_config.get("volume"),
            "speed": tts_config.get("speed"),
        })


def create_llm_service(llm_config):
    """Create LLM service based on configuration"""
    model = llm_config.get("model", "openai/gpt-4o")
    base_url = llm_config.get("base_url", "https://openrouter.ai/api/v1")
    api_key = llm_config.get("api_key") or os.getenv("OPENROUTER_API_KEY")
    temperature = llm_config.get("temperature", 0.2)
    top_p = llm_config.get("top_p", 0.9)
    frequency_penalty = llm_config.get("frequency_penalty", 0)
    presence_penalty = llm_config.get("presence_penalty", 0)
    seed = llm_config.get("seed", 42)
    max_completion_tokens = llm_config.get("max_completion_tokens", 256)
    llm_timeout_secs = llm_config.get("llm_timeout_secs", LLM_TIMEOUT_SECS_DEFAULT)
    retry_on_timeout = llm_config.get("retry_on_timeout", LLM_RETRY_ON_TIMEOUT_DEFAULT)

    input_params_kwargs = {
        "temperature": temperature,
        "top_p": top_p,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
        "seed": seed,
        "max_completion_tokens": max_completion_tokens,
    }

    if llm_config.get("provider") in {"openrouter", "azure"}:   #临时兼容，等后端接口支持后可去掉 openrouter
        target = select_azure_llm_target()
        return AzureLLMService(
            api_key=target["api_key"],
            endpoint=target["endpoint"],
            api_version=target["api_version"],
            model=target["deployment"],
            retry_timeout_secs=llm_timeout_secs,
            retry_on_timeout=retry_on_timeout,
            params=OpenAILLMService.InputParams(**input_params_kwargs),
        )

    return OpenAILLMService(
        api_key=api_key,
        model=model,
        base_url=base_url,
        retry_timeout_secs=llm_timeout_secs,
        retry_on_timeout=retry_on_timeout,
        params=OpenAILLMService.InputParams(**input_params_kwargs),
    )


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
    work_order_id = variables.get("work_order_id") or (business_id.split("___")[0] if business_id else "")
    variables['work_order_id'] = work_order_id
    country = variables.get("country", "mexico")
    contact_time = utc2tz(time.time(), country)
    variables['contact_time'] = contact_time

    tenant_id = variables.get("tenant_id", "100035")
    app_type = variables.get("app_type", "")
    outbound_source = variables.get("outbound_source") or "hermes"
    task_type = "collection"
    follower_id = "待传入"
    dial_schedule = ["待传入"]
    customer_group = "待传入"
    has_sms = 0
    has_app_push = 0

    platform = variables.get("platform", "CPU")
    is_round_robin = 0 #_coerce_int(variables.get("is_round_robin", 1), 1)
    is_human_assist = 1 #_coerce_int(variables.get("is_human_assist", 1), 1)

    robot_code = config.get("code", "")
    robot_name = config.get("name", "")
    org_code = config.get("orgCode", "")
    org_name = config.get("orgName", "")

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
        asr_config["model"] = _resolve_deepgram_asr_model(robot_code)

        logging.info(f"ASR Config: {asr_config}")
        logging.info(f"TTS Config: {tts_config}")
        logging.info(f"LLM Config: {llm_config}")

        system_prompt_content = config_manager.get_prompt_config()
        metrics_collector = CallMetricsCollector(
            call_uuid=call_uuid,
            business_id=business_id,
            tenant_id=tenant_id,
            app_type=app_type,
            work_order_id=work_order_id,
            outbound_source=outbound_source,
            platform=platform,
            is_round_robin=is_round_robin,
            is_human_assist=is_human_assist,
            robot_code=robot_code,
            robot_name=robot_name,
            org_code=org_code,
            org_name=org_name,
            country=country,
            task_type=task_type,
            follower_id=follower_id,
            dial_schedule=dial_schedule,
            customer_group=customer_group,
            has_sms=has_sms,
            has_app_push=has_app_push,
            task_start_time=datetime.datetime.now(),
            asr_info={**asr_config, "sample_rate": 8000},
            llm_info=llm_config,
            llm_prompt=system_prompt_content,
            tts_info={**tts_config, "sample_rate": 8000},
        )

        # Create services
        stt_service = create_stt_service(asr_config)
        tts_service = create_tts_service(tts_config)
        llm_service = create_llm_service(llm_config)
        attach_llm_retry_counter(llm_service, metrics_collector)

        # Track TTS reconnects via on_connected event. The first connection is
        # normal startup; subsequent connected events are reconnects.
        if hasattr(tts_service, "event_handler"):
            _tts_connected_once = False

            @tts_service.event_handler("on_connected")
            async def _on_tts_connected(service):
                nonlocal _tts_connected_once
                if _tts_connected_once:
                    metrics_collector.increment_tts_reconnect()
                else:
                    _tts_connected_once = True

        # Track ASR reconnects after Deepgram connection errors.
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
                        "ASR reconnect counted after Deepgram connection error. "
                        f"asr_reconnect_count={metrics_collector.asr_reconnect_count}"
                    )
                    _stt_reconnect_state["saw_connection_error"] = False

            async def _tracked_stt_on_error(self, *args, **kwargs):
                _stt_reconnect_state["saw_connection_error"] = True
                logging.warning("Deepgram connection error detected; next successful _connect will count as ASR reconnect")
                await _original_stt_on_error(*args, **kwargs)

            stt_service._connect = MethodType(_tracked_stt_connect, stt_service)
            stt_service._on_error = MethodType(_tracked_stt_on_error, stt_service)

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

        async def router_hangup_cancel():
            await queue_end_frame("router_on_hangup")

        async def send_protocol_event(
            event_payload: dict[str, Any],
            *,
            description: str,
        ) -> bool:
            return await safe_send_ws_message(
                lambda: transport.output().send_message(
                    OutputTransportMessageUrgentFrame(message=event_payload)
                ),
                websocket=websocket,
                description=description,
                logger=logging.getLogger(__name__),
                on_disconnect=partial(queue_end_frame, "protocol_send_disconnected"),
            )

        async def send_text_event(
            text: str,
            role: str = Role.ROBOT,
            *,
            source: str = "unknown",
        ) -> bool:
            """Send a protocol text event and log both the attempt and result."""
            event_payload = create_text_event(
                call_uuid=call_uuid,
                task_uuid=task_uuid,
                business_id=business_id,
                text=text,
                role=role,
            )
            logging.info(
                "Sending copilot text event: "
                "source=%s role=%s text=%r call_uuid=%s task_uuid=%s business_id=%s",
                source,
                role,
                text,
                call_uuid,
                task_uuid,
                business_id,
            )
            sent = await send_protocol_event(
                event_payload,
                description=f"copilot text event source={source} role={role}",
            )
            logging.info(
                "Copilot text event send completed: "
                "source=%s role=%s sent=%s text=%r call_uuid=%s",
                source,
                role,
                sent,
                text,
                call_uuid,
            )
            return sent

        async def send_heartbeat_event() -> bool:
            return await send_protocol_event(
                create_heartbeat_event(
                    call_uuid=call_uuid,
                    task_uuid=task_uuid,
                    business_id=business_id,
                ),
                description="copilot heartbeat event",
            )

        async def send_hangup_event() -> bool:
            return await send_protocol_event(
                create_hangup_event(
                    call_uuid=call_uuid,
                    task_uuid=task_uuid,
                    business_id=business_id,
                ),
                description="copilot hangup event",
            )

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

        # Save intelligent notes and feed the result into process_record metrics.
        collection_note_result = None
        if context is not None:
            try:
                collection_note_result, note_task_type = await intelligent_notes_distribution(
                    context,
                    event,
                    locale=tts_config.get("locale", "es-MX"),
                )
                if collection_note_result and metrics_collector is not None:
                    metrics_collector.set_collection_note_result(collection_note_result, note_task_type)
            except Exception as e:
                logging.error(f"Error in intelligent_notes_distribution: {e}")

        session_context["is_ended"] = True
        user_name = variables.get("user_name")
        app_type = variables.get("app_type")
        remaining_amount = variables.get("remaining_amount")
        country = variables.get("country", "mexico")

        # Save conversation if needed
        if mode != 'local' and business_id:
            try:
                context_json = json.dumps(
                    message_history,
                    ensure_ascii=False
                )
                insert_sql = """
                    INSERT INTO context_log
                    (business_id, context, ctime1, uuid, user_name, app_type, remaining_amount, country)
                    VALUES
                    (:business_id, :context, :ctime1, :uuid, :user_name, :app_type, :remaining_amount, :country)
                """
                params = {
                    'business_id': business_id,
                    'context': context_json,
                    'ctime1': int(time.time() * 1000),
                    'uuid': call_uuid,
                    'user_name':user_name,
                    'app_type': app_type,
                    'remaining_amount':remaining_amount,
                    'country':country
                }
                MysqlClient.execute(insert_sql, params)
            except Exception as e:
                logging.error(f"Error saving conversation: {e}")

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
