import asyncio
import os

from dotenv import load_dotenv

from robot_config_service.application.webhooks import WebhookWorker
from robot_config_service.infrastructure.database import Database
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from robot_config_service.infrastructure.webhooks.security import CallbackPolicy, SecureWebhookSender
from robot_config_service.settings import Settings


load_dotenv()


def _csv(name):
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


async def run_webhook_worker():
    settings = Settings.from_env()
    settings.validate()
    database = Database.create(settings.database_url)
    database.verify()
    policy = CallbackPolicy(
        allowed_hostnames=_csv("FASTCALL_CALLBACK_ALLOWED_HOSTNAMES"),
        allowed_cidrs=_csv("FASTCALL_CALLBACK_ALLOWED_CIDRS"),
    )
    worker = WebhookWorker(
        lambda: SqlAlchemyUnitOfWork(database.session_factory()),
        SecureWebhookSender(policy),
        WebhookWorker.secrets_from_env(),
    )
    try:
        await worker.run()
    finally:
        database.close()


if __name__ == "__main__":
    asyncio.run(run_webhook_worker())
