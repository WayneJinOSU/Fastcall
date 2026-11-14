import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall_core.application.call_results import FinalizeCallUseCase
from fastcall_core.application.dto import utcnow
from fastcall_core.infrastructure.orm import Base
from fastcall_core.infrastructure.outbound_orm import WebhookDeliveryModel
from fastcall.infrastructure.security import PhoneHmac, SecretCodec
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall.main import create_app
from fastcall.settings import Settings
from tests.unit.fastcall.fixtures import valid_config


class FakeReviewClient:
    def __init__(self):
        self.calls = []

    async def review(self, call_id, payload, actor):
        self.calls.append((call_id, payload, actor))
        return SimpleNamespace(
            content=b'{"status":"reviewed"}', status_code=202,
            headers={"content-type": "application/json"},
        )


class OutboundApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        settings = Settings(
            "test", "mysql+pymysql://unused:unused@localhost/unused",
            "management-token", "resolve-token",
            openapi_token="open-token", admin_token="admin-token",
            adapter_token="adapter-token", agent_token="agent-token", media_token="media-token",
        )
        app = create_app(
            settings=settings,
            uow_factory=lambda: SqlAlchemyUnitOfWork(self.sessions()),
            validate_startup=False,
            secret_codec=SecretCodec({"v1": b"k" * 32}, "v1"),
            phone_hmac=PhoneHmac({"v1": b"h" * 32}, "v1"),
        )
        self.client = TestClient(app)
        self.management = {"Authorization": "Bearer management-token", "X-Actor-Id": "user-1"}
        self.open = {"Authorization": "Bearer open-token"}
        self.admin = {"Authorization": "Bearer admin-token", "X-Actor-Id": "reviewer"}
        self._publish_robot()

    def tearDown(self):
        self.engine.dispose()

    def _publish_robot(self):
        response = self.client.post("/api/v1/robot-apps", headers=self.management, json={
            "app_code": "example-standard-en", "name": "Standard English", "config": valid_config(),
        })
        app_id = response.json()["app_id"]
        report = self.client.post(
            f"/api/v1/robot-apps/{app_id}/validate", headers=self.management,
            json={"draft_revision": 1},
        ).json()
        response = self.client.post(
            f"/api/v1/robot-apps/{app_id}/publish", headers=self.management,
            json={"draft_revision": 1, "config_hash": report["config_hash"]},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _task_payload(self):
        return {
            "name": "Campaign", "robot_code": "example-standard-en",
            "timezone": "Asia/Shanghai", "default_country_code": "1",
            "max_concurrent_calls": 2, "calls_per_minute": 30,
            "retry_policy": {
                "max_retries": 1, "retry_intervals_sec": [60], "retry_on": ["NO_ANSWER"]
            },
            "callback_url": "https://example.test/callback", "callback_secret_ref": "crm",
        }

    def _create_task(self):
        response = self.client.put(
            "/openapi/v1/outbound-tasks/customer-task-1", headers=self.open,
            json=self._task_payload(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _import(self, task_id, contacts):
        return self.client.post(
            f"/openapi/v1/outbound-tasks/{task_id}/contacts", headers=self.open,
            json={"contacts": contacts},
        )

    def test_task_contact_start_pause_resume_and_cancel(self):
        task = self._create_task()
        repeated = self.client.put(
            "/openapi/v1/outbound-tasks/customer-task-1", headers=self.open,
            json=self._task_payload(),
        )
        self.assertEqual(repeated.json()["task_id"], task["task_id"])
        changed = self._task_payload()
        changed["name"] = "Other"
        self.assertEqual(self.client.put(
            "/openapi/v1/outbound-tasks/customer-task-1", headers=self.open, json=changed,
        ).status_code, 409)
        response = self._import(task["task_id"], [
            {"customer_contact_id": "c1", "phone": "+12025550101", "variables": {"user_name": "Alice"}},
            {"customer_contact_id": "c2", "phone": "+12025550101", "variables": {"user_name": "Bob"}},
        ])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["accepted_contacts"]), 1)
        self.assertEqual(response.json()["duplicate_contacts"][0]["error_code"], "DUPLICATE_PHONE")
        started = self.client.post(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/start", headers=self.open,
            json={"expected_state_version": 1},
        )
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["status"], "running")
        self.assertIsNotNone(started.json()["robot_version_id"])
        self.assertEqual(self._import(task["task_id"], [
            {"customer_contact_id": "c3", "phone": "+12025550103", "variables": {"user_name": "C"}}
        ]).status_code, 400)
        paused = self.client.post(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/pause", headers=self.open,
            json={"expected_state_version": started.json()["state_version"]},
        )
        self.assertEqual(paused.json()["status"], "paused")
        resumed = self.client.post(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/resume", headers=self.open,
            json={"expected_state_version": paused.json()["state_version"]},
        )
        cancelled = self.client.post(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/cancel", headers=self.open,
            json={"expected_state_version": resumed.json()["state_version"]},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        contact = self.client.get(
            "/openapi/v1/task-contacts/contact_missing", headers=self.open
        )
        self.assertEqual(contact.status_code, 404)
        uow = SqlAlchemyUnitOfWork(self.sessions())
        self.assertEqual(uow.session.query(WebhookDeliveryModel).count(), 2)
        self.assertEqual(uow.contacts.get_by_customer_id(task["task_id"], "c1").status, "cancelled")
        uow.close()

    def test_keyset_cursor_binds_filters_and_authentication_is_separate(self):
        task = self._create_task()
        self._import(task["task_id"], [
            {"customer_contact_id": "c1", "phone": "+12025550101", "variables": {"user_name": "A"}},
            {"customer_contact_id": "c2", "phone": "+12025550102", "variables": {"user_name": "B"}},
        ])
        first = self.client.get(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/contacts?limit=1", headers=self.open
        )
        self.assertEqual(first.status_code, 200, first.text)
        cursor = first.json()["next_cursor"]
        self.assertTrue(cursor)
        second = self.client.get(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/contacts?limit=1&cursor={cursor}",
            headers=self.open,
        )
        self.assertEqual(second.json()["items"][0]["customer_contact_id"], "c2")
        invalid = self.client.get(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/contacts?limit=1&cursor={cursor}&status=pending",
            headers=self.open,
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.client.get(
            f"/openapi/v1/outbound-tasks/{task['task_id']}", headers=self.management
        ).status_code, 401)

    def test_review_proxy_preserves_status_payload_and_actor(self):
        client = FakeReviewClient()
        self.client.app.state.review_client = client
        response = self.client.post(
            "/adminapi/v1/calls/call_1/review",
            headers=self.admin,
            json={
                "expected_state_version": 1, "resolution": "failed",
                "result_code": "CONFIRMED_NOT_DIALED", "evidence_type": "CDR",
                "evidence_ref": "cdr-1", "note": "Verified", "force_release": False,
            },
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"status": "reviewed"})
        self.assertEqual(client.calls[0][0], "call_1")
        self.assertEqual(client.calls[0][2], "reviewer")
        self.assertEqual(client.calls[0][1]["resolution"], "failed")

    def test_resume_completes_task_when_last_call_finished_while_paused(self):
        task = self._create_task()
        self._import(task["task_id"], [
            {"customer_contact_id": "c1", "phone": "+12025550101", "variables": {"user_name": "A"}}
        ])
        started = self.client.post(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/start", headers=self.open,
            json={"expected_state_version": 1},
        ).json()
        uow = SqlAlchemyUnitOfWork(self.sessions())
        contact = uow.contacts.get_by_customer_id(task["task_id"], "c1")
        uow.calls.reserve(contact.id, "call_1", "origin_1", utcnow(), utcnow())
        uow.commit()
        uow.close()
        paused = self.client.post(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/pause", headers=self.open,
            json={"expected_state_version": started["state_version"]},
        ).json()
        finalizer_uow = SqlAlchemyUnitOfWork(self.sessions())
        FinalizeCallUseCase(finalizer_uow).execute(
            "call_1", "failed", "CALL_REJECTED", "failed"
        )
        self.assertEqual(finalizer_uow.tasks.get(task["task_id"]).status, "paused")
        finalizer_uow.close()
        resumed = self.client.post(
            f"/openapi/v1/outbound-tasks/{task['task_id']}/resume", headers=self.open,
            json={"expected_state_version": paused["state_version"]},
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "completed")
        verify = SqlAlchemyUnitOfWork(self.sessions())
        self.assertEqual(verify.session.query(WebhookDeliveryModel).count(), 2)
        verify.close()


if __name__ == "__main__":
    unittest.main()
