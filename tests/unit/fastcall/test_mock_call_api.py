import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastcall.api.console.mock_calls import router
from fastcall.application.mock_calls import MockCallTicketStore


class MockCallApiTests(unittest.TestCase):
    def test_invalid_ticket_does_not_remove_or_cancel_valid_session(self):
        app = FastAPI()
        app.state.settings = SimpleNamespace(
            console_session_secret="secret", adapter_token="adapter",
            adapter_internal_url="http://adapter:8767",
        )
        app.state.mock_call_tickets = MockCallTicketStore()
        app.include_router(router)
        ticket = app.state.mock_call_tickets.create("task-1", "tester")
        cleanup = AsyncMock()
        client = TestClient(app)
        with patch("fastcall.api.console.mock_calls.decode_session_token", return_value=SimpleNamespace(actor="tester")), patch(
            "fastcall.api.console.mock_calls._cancel_active_mock", cleanup
        ):
            with client.websocket_connect("/console/api/v1/mock-calls/task-1/ws?ticket=invalid") as ws:
                message = ws.receive_json()
                self.assertEqual(message["code"], "MOCK_CALL_SESSION_INVALID")
        cleanup.assert_not_called()
        claimed = app.state.mock_call_tickets.claim("task-1", ticket.ticket, "tester")
        self.assertEqual(claimed.task_id, "task-1")


if __name__ == "__main__":
    unittest.main()
