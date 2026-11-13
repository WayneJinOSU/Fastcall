import asyncio
import os

from dotenv import load_dotenv

from robot_config_service.application.scheduler import DialScheduler
from robot_config_service.infrastructure.database import Database
from robot_config_service.infrastructure.dispatch_client import AdapterDispatchClient
from robot_config_service.infrastructure.security import PhoneHmac, SecretCodec
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from robot_config_service.settings import Settings


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
        uow_factory, dispatcher, SecretCodec.from_env(), PhoneHmac.from_env(),
        global_max_concurrent=int(os.getenv("FASTCALL_GLOBAL_MAX_CONCURRENT", "50")),
        global_calls_per_minute=int(os.getenv("FASTCALL_GLOBAL_CALLS_PER_MINUTE", "60")),
    )
    try:
        await scheduler.run()
    finally:
        database.close()


if __name__ == "__main__":
    asyncio.run(run_scheduler())
