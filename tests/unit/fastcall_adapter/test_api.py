import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall_adapter.api.app import create_app
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_adapter.settings import Settings
from fastcall_core.infrastructure.crypto import SecretCodec
from fastcall_core.infrastructure.orm import Base


class AdapterApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine)
        settings = Settings(
            "test", "mysql+pymysql://unused:unused@localhost/unused",
            admin_token="admin-token", adapter_token="adapter-token",
            agent_token="agent-token", media_token="media-token",
        )
        app = create_app(
            settings=settings,
            uow_factory=lambda: SqlAlchemyAdapterUnitOfWork(self.sessions()),
            secret_codec=SecretCodec({"v1": b"k" * 32}, "v1"),
            validate_startup=False,
        )
        self.client = TestClient(app)

    def tearDown(self):
        self.engine.dispose()

    def test_internal_credentials_are_not_interchangeable(self):
        path = "/internal/v1/calls/missing/session-claim"
        self.assertEqual(self.client.post(
            path, headers={"Authorization": "Bearer open-token"}
        ).status_code, 401)
        self.assertEqual(self.client.post(
            path, headers={"Authorization": "Bearer adapter-token"}
        ).status_code, 404)
        event_path = "/internal/v1/calls/missing/session-events/ready"
        payload = {
            "event_id": "agent:missing:1:ready", "claim_generation": 1,
            "agent_version": "test", "occurred_at": "2026-01-01T00:00:00Z",
        }
        self.assertEqual(self.client.post(
            event_path, headers={"Authorization": "Bearer adapter-token"}, json=payload,
        ).status_code, 401)


if __name__ == "__main__":
    unittest.main()
