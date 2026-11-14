import asyncio
import os

from dotenv import load_dotenv

from fastcall.application.webhooks import WebhookWorker, result_changed_events_enabled
from fastcall.infrastructure.database import Database
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall.infrastructure.webhooks.security import CallbackPolicy, SecureWebhookSender
from fastcall.settings import Settings


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
        result_changed_enabled=result_changed_events_enabled,
    )
    try:
        await worker.run()
    finally:
        database.close()


if __name__ == "__main__":
    asyncio.run(run_webhook_worker())
