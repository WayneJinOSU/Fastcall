import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from fastcall_adapter.application.health import AdapterHealthService
from fastcall_adapter.settings import Settings


class FakeGateway:
    def __init__(self, fail_capability=None):
        self.fail_capability = fail_capability
        self.calls = []

    async def health_check(self, capability=None):
        self.calls.append(capability)
        if capability == self.fail_capability:
            raise TimeoutError("capability unavailable")
        return True


def settings(**overrides):
    values = {
        "admin_token": "admin-token",
        "adapter_token": "adapter-token",
        "agent_token": "agent-token",
        "media_token": "media-token",
        "freeswitch_sip_gateway": "carrier",
    }
    values.update(overrides)
    return Settings("test", "mysql+pymysql://u:p@host/db", **values)


class AdapterHealthTests(unittest.TestCase):
    def test_sip_failure_makes_service_not_ready(self):
        gateway = FakeGateway(fail_capability="sip")
        service = AdapterHealthService(Mock(), gateway, settings())
        service._agent_health = AsyncMock()
        result = asyncio.run(service.check())
        self.assertEqual(result, {
            "status": "not_ready", "common": "ready", "sip": "not_ready",
        })
        self.assertEqual(gateway.calls, [None, "sip"])

    def test_agent_failure_makes_service_not_ready(self):
        service = AdapterHealthService(Mock(), FakeGateway(), settings())
        service._agent_health = AsyncMock(side_effect=TimeoutError("agent unavailable"))
        result = asyncio.run(service.check())
        self.assertEqual(result, {
            "status": "not_ready", "common": "not_ready", "sip": "not_ready",
        })

    def test_disabling_sip_is_invalid(self):
        with self.assertRaises(RuntimeError):
            settings(sip_enabled=False).validate()


if __name__ == "__main__":
    unittest.main()
