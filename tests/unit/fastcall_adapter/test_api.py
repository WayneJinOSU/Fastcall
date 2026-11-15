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

    def test_mock_websocket_routes_are_not_registered_when_disabled(self):
        from dataclasses import replace

        app = create_app(
            settings=replace(
                self.client.app.state.settings, mock_calls_enabled=False
            ),
            validate_startup=False,
        )
        paths = {route.path for route in app.routes}
        self.assertNotIn("/internal/v1/mock-calls/{task_id}/browser", paths)
        self.assertNotIn("/internal/media/{call_id}/browser", paths)

    def test_media_token_is_accepted_only_as_bearer_header(self):
        from starlette.websockets import WebSocketDisconnect

        cases = (
            ("/internal/media/call-1?token=media-token", {}, 1008),
            (
                "/internal/media/call-1",
                {"Authorization": "Bearer media-token"},
                1011,
            ),
        )
        for url, headers, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(WebSocketDisconnect) as error:
                    with self.client.websocket_connect(url, headers=headers):
                        pass
                self.assertEqual(error.exception.code, expected_code)


if __name__ == "__main__":
    unittest.main()
