import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall_core.infrastructure.orm import Base
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall.main import create_app
from fastcall.settings import Settings

from tests.unit.fastcall.fixtures import valid_config


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        settings = Settings(
            "test", "mysql+pymysql://unused:unused@localhost/unused",
            "management-token", "resolve-token",
        )
        app = create_app(
            settings=settings,
            uow_factory=lambda: SqlAlchemyUnitOfWork(self.sessions()),
            validate_startup=False,
        )
        self.client = TestClient(app)
        self.management = {"Authorization": "Bearer management-token", "X-Actor-Id": "user-1"}
        self.resolve = {"Authorization": "Bearer resolve-token"}

    def tearDown(self):
        self.engine.dispose()

    def create_and_publish(self):
        response = self.client.post("/api/v1/robot-apps", headers=self.management, json={
            "app_code": "example-standard-en", "name": "Standard English", "config": valid_config(),
        })
        self.assertEqual(response.status_code, 201, response.text)
        app_id = response.json()["app_id"]
        report = self.client.post(
            f"/api/v1/robot-apps/{app_id}/validate", headers=self.management,
            json={"draft_revision": 1},
        ).json()
        self.assertTrue(report["valid"], report)
        response = self.client.post(
            f"/api/v1/robot-apps/{app_id}/publish", headers=self.management,
            json={"draft_revision": 1, "config_hash": report["config_hash"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return app_id, response.json()

    def test_management_and_resolve_authentication_are_separate(self):
        self.assertEqual(self.client.get("/api/v1/robot-apps", headers=self.resolve).status_code, 401)
        no_actor = {"Authorization": "Bearer management-token"}
        response = self.client.get("/api/v1/robot-apps", headers=no_actor)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "ACTOR_REQUIRED")
        response = self.client.post(
            "/internal/v1/runtime/resolve", headers=self.management,
            json={"robot_code": "missing"},
        )
        self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/internal/v1/runtime/resolve", headers=self.resolve,
            json={"robot_code": "missing"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "RESOLVE_FAILED")

    def test_management_publish_resolve_disable_and_rollback_round_trip(self):
        app_id, first = self.create_and_publish()
        resolved = self.client.post(
            "/internal/v1/runtime/resolve", headers=self.resolve,
            json={"robot_code": "example-standard-en"},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["app_config"], valid_config())
        self.assertNotIn("environment", resolved.json())
        self.assertEqual(resolved.json()["runtime_catalog_version"], "2026-11-12.3")

        changed = valid_config()
        changed["prompt"]["system_template"] = "Changed {{user_name}}"
        response = self.client.put(
            f"/api/v1/robot-apps/{app_id}/draft", headers=self.management,
            json={"draft_revision": 1, "config": changed},
        )
        self.assertEqual(response.status_code, 200, response.text)
        report = self.client.post(
            f"/api/v1/robot-apps/{app_id}/validate", headers=self.management,
            json={"draft_revision": 2},
        ).json()
        second = self.client.post(
            f"/api/v1/robot-apps/{app_id}/publish", headers=self.management,
            json={"draft_revision": 2, "config_hash": report["config_hash"]},
        ).json()
        versions = self.client.get(f"/api/v1/robot-apps/{app_id}/versions", headers=self.management)
        self.assertEqual(len(versions.json()["items"]), 2)
        detail = self.client.get(
            f"/api/v1/robot-apps/{app_id}/versions/{second['robot_app_version_id']}",
            headers=self.management,
        )
        self.assertEqual(detail.json()["app_config"], changed)
        diff = self.client.get(f"/api/v1/robot-apps/{app_id}/diff?from=1&to=2", headers=self.management)
        self.assertIn("Changed", diff.json()["unified_diff"])

        disabled = self.client.put(
            f"/api/v1/robot-apps/{app_id}/enabled", headers=self.management,
            json={"enabled": False},
        )
        self.assertTrue(disabled.status_code == 200)
        self.assertEqual(self.client.post(
            "/internal/v1/runtime/resolve", headers=self.resolve,
            json={"robot_code": "example-standard-en"},
        ).status_code, 404)
        self.client.put(
            f"/api/v1/robot-apps/{app_id}/enabled", headers=self.management,
            json={"enabled": True},
        )
        rollback = self.client.post(
            f"/api/v1/robot-apps/{app_id}/rollback", headers=self.management,
            json={"robot_app_version_id": first["robot_app_version_id"]},
        )
        self.assertEqual(rollback.status_code, 200, rollback.text)
        resolved = self.client.post(
            "/internal/v1/runtime/resolve", headers=self.resolve,
            json={"robot_code": "example-standard-en"},
        ).json()
        self.assertEqual(resolved["robot_app_version_id"], first["robot_app_version_id"])

    def test_invalid_publish_and_stale_draft_return_structured_errors(self):
        app_id, _ = self.create_and_publish()
        stale = self.client.put(
            f"/api/v1/robot-apps/{app_id}/draft", headers=self.management,
            json={"draft_revision": 2, "config": valid_config()},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "DRAFT_REVISION_CONFLICT")
        mismatch = self.client.post(
            f"/api/v1/robot-apps/{app_id}/publish", headers=self.management,
            json={"draft_revision": 1, "config_hash": "sha256:" + "0" * 64},
        )
        self.assertEqual(mismatch.status_code, 409)
        self.assertEqual(mismatch.json()["error"]["code"], "CONFIG_HASH_MISMATCH")


if __name__ == "__main__":
    unittest.main()
