import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall.api.console.auth import hash_password
from fastcall_core.application.dto import utcnow
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.infrastructure.orm import Base
from fastcall.infrastructure.security import PhoneHmac, SecretCodec
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall.main import create_app
from fastcall.settings import Settings
from tests.unit.fastcall.fixtures import valid_config


class FakeAgentReportClient:
    def __init__(self):
        self.filters = None
        self.error = None

    async def list_records(self, filters):
        self.filters = filters
        if self.error:
            raise self.error
        return {"items": [{"record_id": 7, "call_uuid": "call_1"}], "summary": {}, "next_cursor": None}

    async def get_summary(self, filters):
        self.filters = filters
        return {"logical_calls": 2, "voicemail_rate": 50}

    async def get_daily(self, filters):
        self.filters = filters
        return {"items": [], "start_at": filters["start_at"], "end_at": filters["end_at"]}

    async def get_facets(self):
        return {"robot_codes": ["robot-1"], "bot_modes": ["standard"]}

    async def get_record(self, record_id):
        if self.error:
            raise self.error
        return {"record_id": record_id, "call_uuid": "call_1"}


class ConsoleOutboundApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.agent = FakeAgentReportClient()
        settings = Settings(
            "test", "mysql+pymysql://unused:unused@localhost/unused",
            "management-token", "resolve-token", "admin",
            hash_password("correct-password"), "x" * 48,
            openapi_token="open-token", admin_token="admin-token",
            adapter_token="adapter-token", agent_token="agent-token", media_token="media-token",
        )
        self.app = create_app(
            settings=settings,
            uow_factory=lambda: SqlAlchemyUnitOfWork(self.sessions()),
            validate_startup=False,
            secret_codec=SecretCodec({"v1": b"k" * 32}, "v1"),
            phone_hmac=PhoneHmac({"v1": b"h" * 32}, "v1"),
            agent_report_client=self.agent,
        )
        self.client = TestClient(self.app)
        self.management = {"Authorization": "Bearer management-token", "X-Actor-Id": "user-1"}
        self.open = {"Authorization": "Bearer open-token"}
        login = self.client.post(
            "/console/api/v1/login",
            json={"actor_id": "admin", "password": "correct-password"},
        )
        self.csrf = login.json()["csrf_token"]
        self.write = {"X-CSRF-Token": self.csrf}
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

    def _create_task(self, customer_id="customer-task-1"):
        response = self.client.put(
            f"/openapi/v1/outbound-tasks/{customer_id}", headers=self.open,
            json={
                "name": "Campaign", "robot_code": "example-standard-en",
                "timezone": "Asia/Shanghai", "default_country_code": "1",
                "max_concurrent_calls": 2, "calls_per_minute": 30,
                "retry_policy": {
                    "max_retries": 1, "retry_intervals_sec": [60], "retry_on": ["NO_ANSWER"]
                },
                "callback_url": "https://example.test/callback", "callback_secret_ref": "crm",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _import_contact(self, task_id):
        response = self.client.post(
            f"/openapi/v1/outbound-tasks/{task_id}/contacts", headers=self.open,
            json={"contacts": [{
                "customer_contact_id": "c1", "phone": "+12025550101",
                "variables": {"user_name": "Alice"},
            }]},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _reserve_call(self, task_id):
        uow = SqlAlchemyUnitOfWork(self.sessions())
        contact = uow.contacts.get_by_customer_id(task_id, "c1")
        uow.calls.reserve(contact.id, "call_1", "origin_1", utcnow(), utcnow())
        uow.commit()
        uow.close()

    def test_read_models_are_aggregated_and_sanitized(self):
        task = self._create_task()
        self._import_contact(task["task_id"])
        started = self.client.post(
            f"/console/api/v1/outbound-tasks/{task['task_id']}/start",
            headers=self.write, json={"expected_state_version": 1},
        )
        self.assertEqual(started.status_code, 200, started.text)
        self._reserve_call(task["task_id"])
        response = self.client.get("/console/api/v1/outbound-tasks")
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertEqual(item["robot_name"], "Standard English")
        self.assertEqual(item["contact_statistics"], {"calling": 1})
        self.assertEqual(item["active_call_count"], 1)
        self.assertNotIn("callback_secret_ref", item)
        self.assertNotIn("callback_url", item)
        detail = self.client.get(f"/console/api/v1/outbound-tasks/{task['task_id']}")
        self.assertEqual(detail.json()["task_id"], task["task_id"])
        contacts = self.client.get(
            f"/console/api/v1/outbound-tasks/{task['task_id']}/contacts"
        )
        self.assertEqual(contacts.json()["items"][0]["phone_last4"], "0101")
        self.assertNotIn("phone_ciphertext", contacts.text)
        calls = self.client.get(f"/console/api/v1/outbound-tasks/{task['task_id']}/calls")
        self.assertEqual(calls.json()["items"][0]["call_id"], "call_1")
        self.assertNotIn("failure_message", calls.text)
        call = self.client.get("/console/api/v1/calls/call_1")
        self.assertEqual(call.json()["task_id"], task["task_id"])
        self.assertNotIn("failure_message", call.text)

    def test_controls_require_csrf_and_return_console_task(self):
        task = self._create_task()
        self._import_contact(task["task_id"])
        path = f"/console/api/v1/outbound-tasks/{task['task_id']}/start"
        self.assertEqual(self.client.post(path, json={"expected_state_version": 1}).status_code, 403)
        started = self.client.post(path, headers=self.write, json={"expected_state_version": 1})
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.json()["status"], "running")
        self.assertEqual(started.json()["robot_name"], "Standard English")
        paused = self.client.post(
            f"/console/api/v1/outbound-tasks/{task['task_id']}/pause",
            headers=self.write, json={"expected_state_version": started.json()["state_version"]},
        )
        self.assertEqual(paused.json()["status"], "paused")
        conflict = self.client.post(
            f"/console/api/v1/outbound-tasks/{task['task_id']}/resume",
            headers=self.write, json={"expected_state_version": 1},
        )
        self.assertEqual(conflict.status_code, 409)
        resumed = self.client.post(
            f"/console/api/v1/outbound-tasks/{task['task_id']}/resume",
            headers=self.write, json={"expected_state_version": paused.json()["state_version"]},
        )
        cancelled = self.client.post(
            f"/console/api/v1/outbound-tasks/{task['task_id']}/cancel",
            headers=self.write,
            json={"expected_state_version": resumed.json()["state_version"], "hangup_active": True},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")

    def test_task_cursor_binds_filters(self):
        first_task = self._create_task("task-a")
        self._create_task("task-b")
        first = self.client.get("/console/api/v1/outbound-tasks?limit=1")
        self.assertTrue(first.json()["next_cursor"])
        second = self.client.get(
            f"/console/api/v1/outbound-tasks?limit=1&cursor={first.json()['next_cursor']}"
        )
        self.assertNotEqual(first.json()["items"][0]["task_id"], second.json()["items"][0]["task_id"])
        changed = self.client.get(
            f"/console/api/v1/outbound-tasks?limit=1&cursor={first.json()['next_cursor']}&status=draft"
        )
        self.assertEqual(changed.status_code, 400)
        searched = self.client.get("/console/api/v1/outbound-tasks?search=task-a")
        self.assertEqual(searched.json()["items"][0]["task_id"], first_task["task_id"])

    def test_agent_report_proxy_and_independent_error_mapping(self):
        self.assertEqual(
            TestClient(self.app).get("/console/api/v1/agent-call-records").status_code, 401
        )
        response = self.client.get(
            "/console/api/v1/agent-call-records?source=all&call_uuid=call_1"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("source", self.agent.filters)
        self.assertEqual(self.agent.filters["call_uuid"], "call_1")
        detail = self.client.get("/console/api/v1/agent-call-records/7")
        self.assertEqual(detail.json()["record_id"], 7)
        self.agent.error = RobotConfigError("AGENT_REPORT_TIMEOUT", "timeout")
        timeout = self.client.get("/console/api/v1/agent-call-records")
        self.assertEqual(timeout.status_code, 504)
        self.assertEqual(timeout.json()["error"]["code"], "AGENT_REPORT_TIMEOUT")

    def test_agent_summary_daily_and_facets_proxies(self):
        summary = self.client.get(
            "/console/api/v1/agent-call-records/summary?start_at=2026-11-14T00:00:00Z&end_at=2026-11-15T00:00:00Z&robot_code=robot-1"
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["voicemail_rate"], 50)
        daily = self.client.get(
            "/console/api/v1/agent-call-records/daily?start_at=2026-11-14T00:00:00Z&end_at=2026-11-15T00:00:00Z&bot_mode=standard"
        )
        self.assertEqual(daily.status_code, 200, daily.text)
        self.assertEqual(self.agent.filters["bot_mode"], "standard")
        facets = self.client.get("/console/api/v1/agent-call-records/facets")
        self.assertEqual(facets.json()["robot_codes"], ["robot-1"])


if __name__ == "__main__":
    unittest.main()
