import hashlib
import hmac
import json
import time
import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall.api.mock_webhook import router as mock_webhook_router
from fastcall.application.mock_calls import PrepareMockCallUseCase
from fastcall.application.outbound_queries import OutboundQueryUseCase
from fastcall.application.publication import PublicationUseCase
from fastcall.application.robot_apps import RobotAppsUseCase
from fastcall.application.validation import ValidateConfigUseCase
from fastcall.capabilities.loader import load_catalog
from fastcall.infrastructure.security import PhoneHmac, SecretCodec
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall_core.infrastructure.orm import Base
from tests.unit.fastcall.fixtures import valid_config


class MockCallPreparationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.codec = SecretCodec({"v1": b"k" * 32}, "v1")
        self.hmac = PhoneHmac({"v1": b"h" * 32}, "v1")
        self.app = RobotAppsUseCase(self.uow).create(
            "example-standard-en", "Standard English", valid_config(), "tester"
        )
        self.version = PublicationUseCase(
            self.uow, ValidateConfigUseCase(load_catalog())
        ).publish(self.app["app_id"], 1, ValidateConfigUseCase(load_catalog()).execute(valid_config()).config_hash, "tester")

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def use_case(self):
        return PrepareMockCallUseCase(
            self.uow, self.codec, self.hmac,
            callback_url="https://fastcall.test/internal/mock-webhook",
            callback_secret_ref="mock",
        )

    def test_prepare_pins_release_and_arm_uses_normal_task_states(self):
        result = self.use_case().prepare(
            self.app["app_id"], self.version["robot_app_version_id"],
            {"user_name": "Alice"}, "mock:request-1", "tester",
        )
        task = self.uow.tasks.get(result["task_id"])
        self.assertEqual(task.origin, "mock_call")
        self.assertEqual(task.status, "draft")
        self.assertEqual(task.robot_version_id, self.version["robot_app_version_id"])
        contacts = self.uow.queries.all_contacts(task.id)
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0].status, "pending")
        self.assertEqual(OutboundQueryUseCase(self.uow).list_tasks(None, None, None, None, 20)["items"], [])

        armed = self.use_case().arm(task.id, "tester")
        self.assertEqual(armed["status"], "running")
        self.assertEqual(self.use_case().arm(task.id, "tester")["status"], "running")

    def test_release_change_is_rejected(self):
        with self.assertRaises(Exception) as raised:
            self.use_case().prepare(
                self.app["app_id"], "appver_missing", {"user_name": "Alice"},
                "mock:request-2", "tester",
            )
        self.assertEqual(raised.exception.code, "MOCK_CALL_RELEASE_CHANGED")

    def test_disabled_robot_is_rejected_before_task_creation(self):
        RobotAppsUseCase(self.uow).set_enabled(self.app["app_id"], False, "tester")
        with self.assertRaises(Exception) as raised:
            self.use_case().prepare(
                self.app["app_id"], self.version["robot_app_version_id"],
                {"user_name": "Alice"}, "mock:disabled-1", "tester",
            )
        self.assertEqual(raised.exception.code, "ROBOT_DISABLED")
        self.assertIsNone(self.uow.tasks.get_by_customer_id("mock:disabled-1"))


class MockWebhookTests(unittest.TestCase):
    def test_receiver_verifies_existing_signature_contract(self):
        app = FastAPI()
        app.state.settings = SimpleNamespace(mock_callback_secret_ref="mock")
        app.state.mock_webhook_secrets = {"mock": "secret"}
        app.include_router(mock_webhook_router)
        client = TestClient(app)
        payload = {"event_type": "task.completed", "task_id": "task-1"}
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            b"secret", timestamp.encode() + b".event-1." + body, hashlib.sha256
        ).hexdigest()
        response = client.post("/internal/mock-webhook", content=body, headers={
            "Content-Type": "application/json",
            "X-FastCall-Event-Id": "event-1",
            "X-FastCall-Timestamp": timestamp,
            "X-FastCall-Signature": f"sha256={signature}",
        })
        self.assertEqual(response.status_code, 204)


if __name__ == "__main__":
    unittest.main()
