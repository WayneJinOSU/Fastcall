import logging


def register_telephony_handlers(router, transport, *, on_answer, on_end):
    async def log_frame(frame):
        logging.info("%s: %s", type(frame).__name__, frame.payload)

    async def log_frame_debug(frame):
        logging.debug("%s: %s", type(frame).__name__, frame.payload)

    async def on_hangup(frame):
        logging.info("on_hangup: %s", frame.payload)
        await on_end("event_on_hangup")

    router.on("on_start", log_frame)
    router.on("on_ring", log_frame)
    router.on("on_answer", on_answer)
    router.on("on_dtmf", log_frame)
    router.on("on_stat", log_frame_debug)
    router.on("on_talk", log_frame_debug)
    router.on("on_hangup", on_hangup)

    if hasattr(transport, "event_handler"):
        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport_obj, client):
            logging.info("on_client_disconnected: %s", client)
            pipecat_client = getattr(transport_obj, "_client", None)
            if pipecat_client:
                pipecat_client._closing = True
            await on_end("client_disconnected")

        @transport.event_handler("on_session_timeout")
        async def on_session_timeout(transport_obj, client):
            logging.info("on_session_timeout: %s", client)
            await on_end("session_timeout")
