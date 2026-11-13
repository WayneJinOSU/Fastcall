import asyncio
import logging
import os
import uuid

from robot_config_service.application.dto import utcnow


logger = logging.getLogger(__name__)


class WebhookWorker:
    def __init__(
        self, uow_factory, sender, secrets, *, max_attempts=10,
        poll_interval_sec=1, clock=utcnow, owner=None,
    ):
        self.uow_factory = uow_factory
        self.sender = sender
        self.secrets = secrets
        self.max_attempts = max_attempts
        self.poll_interval_sec = poll_interval_sec
        self.clock = clock
        self.owner = owner or uuid.uuid4().hex

    @classmethod
    def secrets_from_env(cls):
        import json
        value = json.loads(os.getenv("FASTCALL_WEBHOOK_SECRETS_JSON", "{}"))
        if not isinstance(value, dict):
            raise RuntimeError("FASTCALL_WEBHOOK_SECRETS_JSON must be an object")
        return value

    async def run_once(self):
        uow = self.uow_factory()
        try:
            deliveries = uow.webhook_worker.claim_due(self.clock(), self.owner)
            uow.commit()
        finally:
            uow.close()
        results = []
        for delivery in deliveries:
            outcome_uow = self.uow_factory()
            try:
                task = outcome_uow.tasks.get(delivery.task_id)
                secret = self.secrets.get(task.callback_secret_ref or "")
                if not secret:
                    raise RuntimeError("Webhook secret reference is unavailable")
                status = await self.sender.send(
                    delivery.endpoint_url, delivery.payload_json, delivery.event_id, secret
                )
                if 200 <= status < 300:
                    outcome_uow.webhook_worker.mark_delivered(
                        delivery.id, self.owner, status, self.clock()
                    )
                    outcome = "delivered"
                else:
                    outcome = outcome_uow.webhook_worker.mark_retry(
                        delivery.id, self.owner, f"HTTP {status}", self.clock(),
                        max_attempts=self.max_attempts, http_status=status,
                    )
                outcome_uow.commit()
                if outcome == "failed":
                    logger.error(
                        "Webhook delivery failed permanently id=%s attempts=%s",
                        delivery.id, delivery.attempt_count,
                    )
                results.append((delivery.id, outcome))
            except Exception as error:
                outcome_uow.rollback()
                outcome_uow.webhook_worker.mark_retry(
                    delivery.id, self.owner, type(error).__name__, self.clock(),
                    max_attempts=self.max_attempts,
                )
                outcome_uow.commit()
                results.append((delivery.id, "retrying"))
            finally:
                outcome_uow.close()
        return results

    async def run(self, stop_event=None):
        stop_event = stop_event or asyncio.Event()
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_interval_sec)
            except asyncio.TimeoutError:
                pass
