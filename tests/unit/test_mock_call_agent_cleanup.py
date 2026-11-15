import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collection_agents.server.routes import _run_v2_bot
from collection_agents.session_registry import SessionSlotRegistry


class DisconnectedLifecycle:
    def __init__(self, *args):
        pass

    async def attached(self):
        raise RuntimeError("disconnected while awaiting attached ACK")

    async def failed(self, message):
        pass


class MockCallAgentCleanupTests(unittest.TestCase):
    def test_attached_ack_disconnect_releases_slot(self):
        registry = SessionSlotRegistry()
        app = SimpleNamespace(state=SimpleNamespace(session_slot_registry=registry))
        websocket = SimpleNamespace(headers={"authorization": "Bearer token"})
        call_data = {"data": {
            "callUUID": "call-1", "claimGeneration": 1, "configHash": "sha256:test",
            "config": {"name": "standard"},
        }}
        with patch.dict("os.environ", {"FASTCALL_AGENT_TOKEN": "token"}), patch(
            "collection_agents.server.routes.SessionLifecycle", DisconnectedLifecycle
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(_run_v2_bot(websocket, app, call_data))
        self.assertFalse(asyncio.run(registry.has_slot("call-1")))


if __name__ == "__main__":
    unittest.main()
