import os
import unittest
from unittest.mock import patch

from collection_agents.server.startup_warmup import warm_provider_connections


class StartupWarmupTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_provider_keys_are_skipped(self):
        with patch.dict(
            os.environ,
            {
                "STARTUP_PROVIDER_WARMUP": "1",
                "AZURE_REALTIME_ENDPOINT": "",
                "AZURE_REALTIME_MODEL": "",
                "AZURE_REALTIME_API_KEY": "",
                "DEEPGRAM_API_KEY": "",
                "CARTESIA_API_KEY": "",
            },
        ):
            statuses = await warm_provider_connections()

        self.assertEqual(
            statuses,
            {
                "azure_realtime": "skipped",
                "deepgram": "skipped",
                "cartesia": "skipped",
            },
        )

    async def test_warmup_can_be_disabled(self):
        with patch.dict(os.environ, {"STARTUP_PROVIDER_WARMUP": "0"}):
            statuses = await warm_provider_connections()

        self.assertEqual(statuses, {})


if __name__ == "__main__":
    unittest.main()
