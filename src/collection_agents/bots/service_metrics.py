import logging
from types import MethodType


def track_tts_reconnects(tts_service, metrics_collector):
    connected_once = False

    @tts_service.event_handler("on_connected")
    async def on_connected(service):
        nonlocal connected_once
        if connected_once:
            metrics_collector.increment_tts_reconnect()
        else:
            connected_once = True


def track_stt_reconnects(stt_service, metrics_collector):
    if not hasattr(stt_service, "_connect") or not hasattr(stt_service, "_on_error"):
        return

    state = {"saw_connection_error": False}
    original_connect = stt_service._connect
    original_on_error = stt_service._on_error

    async def tracked_connect(self, *args, **kwargs):
        await original_connect(*args, **kwargs)
        if state["saw_connection_error"]:
            metrics_collector.increment_asr_reconnect()
            logging.info(
                "ASR reconnect counted after connection error. asr_reconnect_count=%d",
                metrics_collector.asr_reconnect_count,
            )
            state["saw_connection_error"] = False

    async def tracked_on_error(self, *args, **kwargs):
        state["saw_connection_error"] = True
        logging.warning("STT connection error; next successful connect counts as reconnect")
        await original_on_error(*args, **kwargs)

    stt_service._connect = MethodType(tracked_connect, stt_service)
    stt_service._on_error = MethodType(tracked_on_error, stt_service)
