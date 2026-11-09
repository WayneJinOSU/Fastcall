import asyncio
import datetime
import logging
import time
from types import MethodType

from collection_agents.bots.standard.grammar import (
    CONFIRMATION_GRAMMAR,
    CONFIRMATION_GRAMMAR_RELEASE_SEC,
)
from collection_agents.metrics.collector import CallMetricsCollector
from collection_agents.services.audio_assets import (
    ensure_voicemail_audio_asset,
    normalize_text as normalize_audio_asset_text,
    split_voicemail_segments,
)
from collection_agents.services.call_config import prepare_text_for_tts
from collection_agents.services.providers import (
    attach_llm_retry_counter,
    create_llm_service,
    create_stt_service,
    create_tts_service,
    resolve_bot_llm_runtime,
    resolve_deepgram_shadow_config,
    resolve_stt_provider,
)
from collection_agents.services.stt_local import (
    DeepgramShadowDecoder,
    VoskFinalCollector,
)


async def create_call_services(call_ctx, session_context, session, call_data):
    config_manager = call_ctx["config_manager"]
    variables = call_ctx["variables"]
    country = call_ctx["country"]
    task_type = call_ctx["task_type"]
    voicemail_config = call_ctx["voicemail_config"]
    voicemail_hangup_on_detect = call_ctx["voicemail_hangup_on_detect"]
    follower_id = call_ctx["follower_id"]
    dial_schedule = call_ctx["dial_schedule"]
    customer_group = call_ctx["customer_group"]
    has_sms = call_ctx["has_sms"]
    has_app_push = call_ctx["has_app_push"]
    tenant_id = call_ctx["tenant_id"]
    app_type = call_ctx["app_type"]
    work_order_id = call_ctx["work_order_id"]
    outbound_source = call_ctx["outbound_source"]
    platform = call_ctx["platform"]
    is_round_robin = call_ctx["is_round_robin"]
    is_human_assist = call_ctx["is_human_assist"]
    robot_code = call_ctx["robot_code"]
    robot_name = call_ctx["robot_name"]
    org_code = call_ctx["org_code"]
    org_name = call_ctx["org_name"]
    call_uuid = call_ctx["call_uuid"]
    business_id = call_ctx["business_id"]

    shadow_config = None
    shadow_decoder = None
    vosk_final_collector = None
    try:
        asr_config = config_manager.get_asr_config()
        tts_config = config_manager.get_tts_config()
        llm_config = config_manager.get_llm_config()

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
        session_context["confirmation_grammar_active"] = stt_provider == "vosk"
        session_context["last_transcription_at"] = 0.0

        def _cancel_grammar_release_task():
            release_task = session_context.get("confirmation_grammar_release_task")
            if release_task and not release_task.done():
                release_task.cancel()
            session_context["confirmation_grammar_release_task"] = None

        async def _release_confirmation_grammar(reason: str):
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
            if not session_context.get("confirmation_grammar_active"):
                return
            _cancel_grammar_release_task()
            session_context["confirmation_grammar_release_task"] = (
                asyncio.create_task(_grammar_release_timer())
            )
        tts_service = create_tts_service(tts_config, session)

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
        llm_service = create_llm_service(llm_config)

        if asr_config.get("provider") == "cartesia":
            _asr_model = asr_config.get("model", "ink-whisper")
        else:
            _asr_model = asr_config.get("model") or "nova-2"
        llm_metrics_config = dict(llm_config)
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
            asr_info={**asr_config, "model": _asr_model, "sample_rate": 8000},
            llm_info=llm_metrics_config,
            llm_prompt=llm_config.get("prompt", ""),
            tts_info={**tts_config, "sample_rate": 8000},
        )
        attach_llm_retry_counter(llm_service, metrics_collector)

        _tts_connected_once = False

        @tts_service.event_handler("on_connected")
        async def _on_tts_connected(service):
            nonlocal _tts_connected_once
            if _tts_connected_once:
                metrics_collector.increment_tts_reconnect()
            else:
                _tts_connected_once = True

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

        logging.info(
            f"🔧 TTS服务配置: provider={tts_config.get('provider')}, voice_id={tts_config.get('voice_id')}, model={tts_config.get('model')}, locale={tts_config.get('locale')}")
        logging.info(f"🔧 ASR服务配置: provider={asr_config.get('provider')}, locale={asr_config.get('locale')}")
        logging.info(f"🔧 LLM服务配置: provider={llm_config.get('provider')}, model={llm_config.get('model')}")
        logging.info("🎯 关键修复: TTS服务直接输出8kHz音频，消除24kHz->8kHz实时降采样造成的音质劣化")

        if hasattr(tts_service, '_sample_rate'):
            logging.info(f"🎵 TTS服务实例采样率: {tts_service._sample_rate}Hz")
        if hasattr(tts_service, 'model_name'):
            logging.info(f"🎵 TTS服务模型: {tts_service.model_name}")
        else:
            logging.info("🎵 TTS服务模型信息不可用")

        logging.info(f"🎵 TTS服务类型: {type(tts_service).__name__}")
        logging.info(f"🎵 TTS服务父类: {[cls.__name__ for cls in type(tts_service).__mro__[1:3]]}")

    except Exception:
        if shadow_decoder is not None:
            try:
                await shadow_decoder.stop()
            except Exception:
                pass
        raise

    return {
        "asr_config": asr_config,
        "tts_config": tts_config,
        "llm_config": llm_config,
        "stt_provider": stt_provider,
        "stt_service": stt_service,
        "tts_service": tts_service,
        "llm_service": llm_service,
        "metrics_collector": metrics_collector,
        "shadow_config": shadow_config,
        "shadow_decoder": shadow_decoder,
        "vosk_final_collector": vosk_final_collector,
        "voicemail_mode": voicemail_mode,
        "fixed_voicemail_live_text": fixed_voicemail_live_text,
        "fixed_voicemail_text": fixed_voicemail_text,
        "fixed_voicemail_cache_enabled": fixed_voicemail_cache_enabled,
        "release_confirmation_grammar": _release_confirmation_grammar,
        "rearm_grammar_release_timer": _rearm_grammar_release_timer,
    }
