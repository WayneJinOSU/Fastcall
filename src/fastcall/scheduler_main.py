import asyncio
import os

from dotenv import load_dotenv

from fastcall.application.scheduler import DialScheduler
from fastcall.infrastructure.database import Database
from fastcall.infrastructure.dispatch_client import AdapterDispatchClient
from fastcall.infrastructure.security import phone_hmac_from_env, secret_codec_from_env
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall.settings import Settings


load_dotenv()


async def run_scheduler():
    settings = Settings.from_env()
    settings.validate()
    database = Database.create(settings.database_url)
    database.verify()
    uow_factory = lambda: SqlAlchemyUnitOfWork(database.session_factory())
    dispatcher = AdapterDispatchClient(
        os.environ["FASTCALL_ADAPTER_INTERNAL_URL"], settings.adapter_token
    )
    scheduler = DialScheduler(
        uow_factory, dispatcher, secret_codec_from_env(), phone_hmac_from_env(),
        global_max_concurrent=int(os.getenv("FASTCALL_GLOBAL_MAX_CONCURRENT", "50")),
        global_calls_per_minute=int(os.getenv("FASTCALL_GLOBAL_CALLS_PER_MINUTE", "60")),
    )
    try:
        await scheduler.run()
    finally:
        database.close()


if __name__ == "__main__":
    asyncio.run(run_scheduler())
