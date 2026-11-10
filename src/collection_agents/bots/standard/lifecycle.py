import asyncio
import datetime
import logging

from pipecat.frames.frames import EndFrame

from collection_agents.notes.call_notes import persist_call_artifacts
from collection_agents.services.stt_local import save_asr_shadow_compare
from collection_agents.transport.websocket_safe_send import send_hangup_signal


async def finalize_call(call_ctx, svc, session_context, context, duration_monitor, flow_watchdog):
    call_ctx = call_ctx or {}
    svc = svc or {}
    call_uuid = call_ctx.get("call_uuid", "")
    robot_code = call_ctx.get("robot_code")
    websocket_client = session_context.get("websocket_client")
    tts_service = svc.get("tts_service")
    stt_service = svc.get("stt_service")
    llm_service = svc.get("llm_service")
    metrics_collector = svc.get("metrics_collector")
    shadow_config = svc.get("shadow_config")
    shadow_decoder = svc.get("shadow_decoder")
    vosk_final_collector = svc.get("vosk_final_collector")

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

    logging.info('finally - standard')
    await send_hangup_signal(websocket_client)

    if metrics_collector:
        metrics_collector.finalize(datetime.datetime.now())

    if metrics_collector:
        logging.info(f"call_metrics: {metrics_collector.to_dict()}")
        metrics_collector.save_to_db()

    await persist_call_artifacts(
        context=context,
        call_data=call_ctx.get("call_data") or {},
        call_uuid=call_uuid,
        locale=(svc.get("tts_config") or {}).get("locale", ""),
        metrics_collector=metrics_collector,
    )
