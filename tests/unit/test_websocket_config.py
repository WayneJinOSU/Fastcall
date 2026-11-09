import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from collection_agents.server.routes import register_routes


class WebSocketConfigTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        register_routes(app)
        self.client = TestClient(app)

    def test_ws_v2_passes_through_delivered_config(self):
        call_data = {
            "action": "start",
            "data": {
                "robot_code": "robot-from-client",
                "callee": "5215500000000",
                "callUUID": "call-1",
                "variables": {"user_name": "Juan"},
                "config": {
                    "code": "robot-from-client",
                    "name": "standard-bot",
                    "promptConfig": {"content": "Client prompt"},
                },
            },
        }
        run_bot = AsyncMock()

        with patch("collection_agents.server.routes.run_bot_beta", run_bot):
            with self.client.websocket_connect("/ws-v2") as websocket:
                websocket.send_json(call_data)

        run_bot.assert_awaited_once()
        self.assertEqual(run_bot.await_args.args[2], call_data)
        self.assertEqual(
            run_bot.await_args.args[2]["data"]["config"]["promptConfig"]["content"],
            "Client prompt",
        )

    def test_copilot_passes_through_delivered_session_config(self):
        event = {
            "event_type": "action.create",
            "session_create": {
                "robot_code": "robot-from-client",
                "variables": {"user_name": "Juan"},
                "config": {
                    "code": "robot-from-client",
                    "name": "copilot-bot",
                    "promptConfig": {"content": "Client prompt"},
                },
            },
        }
        run_bot = AsyncMock()

        with patch("collection_agents.server.routes.run_copilot_bot_beta", run_bot):
            with self.client.websocket_connect("/ws-ai-copilot") as websocket:
                websocket.send_json(event)

        run_bot.assert_awaited_once()
        self.assertEqual(run_bot.await_args.args[1], event)
        self.assertEqual(
            run_bot.await_args.args[1]["session_create"]["config"]["promptConfig"]["content"],
            "Client prompt",
        )


if __name__ == "__main__":
    unittest.main()
