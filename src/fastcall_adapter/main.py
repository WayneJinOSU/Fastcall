import asyncio
import logging
import os
from urllib.parse import urlencode, urlsplit, urlunsplit

import uvicorn
from dotenv import load_dotenv

from fastcall_adapter.api.app import create_app
from fastcall_adapter.application.call_checks import CallCheckUseCase
from fastcall_adapter.application.dispatch import DispatchService
from fastcall_adapter.application.hangup import ReconcileHangupsUseCase
from fastcall_adapter.application.media import AdapterMediaRuntime
from fastcall_adapter.application.telephony_events import FreeSwitchEventService
from fastcall_adapter.infrastructure.crypto import phone_hmac_from_env, secret_codec_from_env
from fastcall_adapter.infrastructure.database import Database
from fastcall_adapter.infrastructure.gateways.esl import FreeSwitchESL
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_adapter.settings import Settings, result_changed_events_enabled
from fastcall_core.application.dto import utcnow


load_dotenv()
logger = logging.getLogger(__name__)


def _media_url():
    url = os.getenv("FASTCALL_MEDIA_WS_URL", "ws://127.0.0.1:8767/internal/media")
    parsed = urlsplit(url)
    query = urlencode({"token": os.environ["FASTCALL_MEDIA_TOKEN"]})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


async def run_adapter():
    logging.basicConfig(level=os.getenv("FASTCALL_LOG_LEVEL", "INFO"))
    settings = Settings.from_env()
    settings.validate()
    database = Database.create(settings.database_url)
    database.verify()
    codec = secret_codec_from_env()
    hmac_keys = phone_hmac_from_env()
    uow_factory = lambda: SqlAlchemyAdapterUnitOfWork(
        database.session_factory(), result_changed_enabled=result_changed_events_enabled
    )
    gateway = FreeSwitchESL(
        os.getenv("FREESWITCH_HOST", "127.0.0.1"),
        int(os.getenv("FREESWITCH_ESL_PORT", "8021")),
        os.environ["FREESWITCH_ESL_PASSWORD"],
        os.environ["FREESWITCH_SIP_GATEWAY"],
        _media_url(),
        dial_mode=os.getenv("FREESWITCH_DIAL_MODE", "gateway"),
    )
    dispatcher = DispatchService(
        uow_factory, gateway, codec, hmac_keys,
        global_max_concurrent=int(os.getenv("FASTCALL_GLOBAL_MAX_CONCURRENT", "50")),
        global_calls_per_minute=int(os.getenv("FASTCALL_GLOBAL_CALLS_PER_MINUTE", "60")),
    )
    media = AdapterMediaRuntime(
        uow_factory, codec, gateway, settings.collection_agent_ws_url, settings.agent_token
    )
    app = create_app(
        settings=settings, database=database, secret_codec=codec, phone_hmac=hmac_keys,
        uow_factory=uow_factory, freeswitch_gateway=gateway, media_runtime=media,
        dispatcher=dispatcher,
    )
    dispatcher.recover_unacknowledged()
    media_tasks = set()

    def media_done(task):
        media_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            logger.error(
                "Media task failed", exc_info=(type(error), error, error.__traceback__)
            )

    async def on_event(event):
        if (
            not event.get("variable_fastcall_call_id")
            or event.get("Unique-ID") != event.get("variable_origination_uuid")
        ):
            return
        event_uow = uow_factory()
        try:
            result = FreeSwitchEventService(event_uow).handle(
                instance_id=os.getenv("FREESWITCH_INSTANCE_ID", "default"),
                stream_epoch=dispatcher.adapter_boot_id,
                event_type=event["Event-Name"],
                event_sequence=event.get("Event-Sequence"),
                channel_uuid=event.get("Unique-ID"),
                origination_uuid=event.get("variable_origination_uuid"),
                fastcall_call_id=event.get("variable_fastcall_call_id"),
                occurred_at=utcnow(),
                raw_hangup_cause=event.get("Hangup-Cause"),
            )
            if event["Event-Name"] == "CHANNEL_ANSWER":
                task = asyncio.create_task(media.run_call(result["call_id"]))
                media_tasks.add(task)
                task.add_done_callback(media_done)
        except Exception:
            logger.exception("FreeSWITCH event processing failed")
        finally:
            event_uow.close()

    hangups = ReconcileHangupsUseCase(uow_factory, gateway)

    async def check_expired():
        while True:
            uow = uow_factory()
            try:
                checks = CallCheckUseCase(uow, gateway)
                await checks.restore_answered_channels()
                checks.expire_checks()
                await media.recover_sessions()
                await hangups.run_once()
            except Exception as error:
                uow.rollback()
                logger.warning("Call check loop iteration failed: %s", type(error).__name__)
            finally:
                uow.close()
            await asyncio.sleep(1)

    server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=int(os.getenv("FASTCALL_ADAPTER_PORT", "8767")), log_level="info"
    ))
    tasks = [asyncio.create_task(server.serve()), asyncio.create_task(gateway.listen_events(on_event)), asyncio.create_task(check_expired())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    server.should_exit = True
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        task.result()


if __name__ == "__main__":
    asyncio.run(run_adapter())
