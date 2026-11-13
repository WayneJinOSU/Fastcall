import asyncio
import datetime
import ipaddress
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from robot_config_service.application.event_worker import EventWorker
from robot_config_service.application.webhooks import WebhookWorker
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.infrastructure.orm import Base
from robot_config_service.infrastructure.outbound_orm import CallEventModel, WebhookDeliveryModel
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from robot_config_service.infrastructure.webhooks.security import (
    CallbackPolicy,
    PinnedNetworkBackend,
    SecureWebhookSender,
)
from tests.unit.robot_config_service.test_outbound_repositories import NOW, task_record


class CallbackSecurityTests(unittest.TestCase):
    def test_private_addresses_require_allowlist_and_metadata_is_always_rejected(self):
        async def scenario():
            private = CallbackPolicy(resolver=lambda host, port: _async_value(["10.0.0.8"]))
            with self.assertRaises(RobotConfigError):
                await private.resolve("https://crm.example/callback")
            allowed = CallbackPolicy(
                allowed_cidrs=["10.0.0.0/8"],
                resolver=lambda host, port: _async_value(["10.0.0.8"]),
            )
            self.assertEqual(
                await allowed.resolve("https://crm.example/callback"),
                ("crm.example", ipaddress.ip_address("10.0.0.8")),
            )
            for address in ("169.254.169.254", "::ffff:169.254.169.254", "127.0.0.1"):
                policy = CallbackPolicy(
                    allowed_hostnames=["crm.example"],
                    resolver=lambda host, port, address=address: _async_value([address]),
                )
                with self.assertRaises(RobotConfigError):
                    await policy.resolve("https://crm.example/callback")

        asyncio.run(scenario())

    def test_network_backend_connects_to_validated_ip(self):
        async def scenario():
            backend = PinnedNetworkBackend("203.0.113.8")
            with patch("httpcore._backends.auto.AutoBackend.connect_tcp", return_value="stream") as connect:
                result = await backend.connect_tcp("crm.example", 443)
                self.assertEqual(result, "stream")
                self.assertEqual(connect.call_args.args[0], "203.0.113.8")

    def test_sender_closes_httpcore_pool(self):
        async def scenario():
            policy = CallbackPolicy(
                allowed_hostnames=["crm.example"],
                resolver=lambda host, port: _async_value(["203.0.113.8"]),
            )
            with patch(
                "robot_config_service.infrastructure.webhooks.security.AsyncConnectionPool"
            ) as pool_type:
                pool = pool_type.return_value
                pool.request = AsyncMock(return_value=type("Response", (), {"status": 204})())
                pool.aclose = AsyncMock()
                status = await SecureWebhookSender(policy).send(
                    "https://crm.example/callback", {"status": "completed"}, "task:1", "secret"
                )
                self.assertEqual(status, 204)
                pool.aclose.assert_awaited_once()

        asyncio.run(scenario())


async def _async_value(value):
    return value


class FakeSender:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.calls = []

    async def send(self, url, payload, event_id, secret):
        self.calls.append((url, payload, event_id, secret))
        return next(self.statuses)


class WebhookWorkerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        task = task_record()
        task.callback_secret_ref = "crm"
        self.uow.tasks.add(task)
        self.uow.session.add(WebhookDeliveryModel(
            id="delivery_1", event_id="task:1", aggregate_type="task", aggregate_id="task_1",
            aggregate_revision=1, task_id="task_1", contact_id=None, call_id=None,
            event_type="task.completed", endpoint_url="https://crm.example/callback",
            payload_json={"status": "completed"}, status="pending", delivery_owner=None,
            delivery_lease_until=None, attempt_count=0, next_delivery_at=NOW,
            last_http_status=None, last_error=None, delivered_at=None, created_at=NOW,
        ))
        self.uow.commit()

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def test_retry_uses_stable_event_and_expired_lease_is_recovered(self):
        async def scenario():
            sender = FakeSender([500, 204])
            now = [NOW]
            worker = WebhookWorker(
                lambda: SqlAlchemyUnitOfWork(self.sessions()), sender, {"crm": "secret"},
                clock=lambda: now[0], owner="worker-1",
            )
            first = await worker.run_once()
            self.assertEqual(first, [("delivery_1", "pending")])
            now[0] = NOW + datetime.timedelta(seconds=10)
            second = await worker.run_once()
            self.assertEqual(second, [("delivery_1", "delivered")])
            self.assertEqual(len(sender.calls), 2)
            self.assertEqual(sender.calls[0][1:], sender.calls[1][1:])
            delivery = self.uow.session.get(WebhookDeliveryModel, "delivery_1")
            self.assertEqual(delivery.status, "delivered")
            self.assertEqual(delivery.attempt_count, 2)

        asyncio.run(scenario())

    def test_expired_webhook_lease_is_reclaimed(self):
        delivery = self.uow.session.get(WebhookDeliveryModel, "delivery_1")
        delivery.status = "delivering"
        delivery.delivery_owner = "crashed"
        delivery.delivery_lease_until = NOW - datetime.timedelta(seconds=1)
        delivery.attempt_count = 1
        self.uow.commit()

        async def scenario():
            worker = WebhookWorker(
                lambda: SqlAlchemyUnitOfWork(self.sessions()), FakeSender([204]),
                {"crm": "secret"}, clock=lambda: NOW, owner="replacement",
            )
            self.assertEqual(await worker.run_once(), [("delivery_1", "delivered")])

        asyncio.run(scenario())
        self.uow.session.refresh(delivery)
        self.assertEqual(delivery.attempt_count, 2)

    def test_expired_event_lease_is_reclaimed(self):
        self.uow.session.add(CallEventModel(
            id="event_row_1", source="freeswitch", event_id="event-1",
            event_type="CHANNEL_ANSWER", task_id="task_1", contact_id="contact_1",
            call_id="call_1", occurred_at=NOW, received_at=NOW, payload_json={},
            payload_hash="a" * 64, process_status="processing", process_owner="crashed",
            process_lease_until=NOW - datetime.timedelta(seconds=1), process_attempts=1,
            next_process_at=None, processed_at=None, last_error=None,
        ))
        self.uow.commit()

        async def handler(uow, event):
            return None

        async def scenario():
            worker = EventWorker(
                lambda: SqlAlchemyUnitOfWork(self.sessions()), handler,
                clock=lambda: NOW, owner="replacement",
            )
            self.assertEqual(await worker.run_once(), [("event_row_1", "processed")])

        asyncio.run(scenario())
        self.assertEqual(
            self.uow.session.get(CallEventModel, "event_row_1").process_status, "processed"
        )


if __name__ == "__main__":
    unittest.main()
