import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastcall import local_main


class LocalMainTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = MagicMock()
        self.server.should_exit = False
        self.server.serve = AsyncMock()
        self.server_patcher = patch.object(local_main.uvicorn, "Server", return_value=self.server)
        self.scheduler_patcher = patch.object(local_main, "run_scheduler")
        self.webhook_patcher = patch.object(local_main, "run_webhook_worker")
        self.server_patcher.start()
        self.scheduler = self.scheduler_patcher.start()
        self.webhook = self.webhook_patcher.start()

    def tearDown(self):
        self.webhook_patcher.stop()
        self.scheduler_patcher.stop()
        self.server_patcher.stop()

    async def _wait_forever(self, name, started, all_started, cleaned, expected_count):
        started.add(name)
        if len(started) == expected_count:
            all_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append(name)

    def _wait_for_components(self, started, all_started, cleaned, expected_count=2):
        async def serve():
            started.add("api")
            if len(started) == expected_count:
                all_started.set()
            try:
                while not self.server.should_exit:
                    await asyncio.sleep(0)
            finally:
                cleaned.append("api")

        async def schedule():
            await self._wait_forever("scheduler", started, all_started, cleaned, expected_count)

        async def deliver_webhooks():
            await self._wait_forever("webhook", started, all_started, cleaned, expected_count)

        self.server.serve.side_effect = serve
        self.scheduler.side_effect = schedule
        self.webhook.side_effect = deliver_webhooks

    async def test_component_failure_cancels_others_and_propagates(self):
        started, all_started, cleaned = set(), asyncio.Event(), []
        self._wait_for_components(started, all_started, cleaned)
        error = RuntimeError("webhook unavailable")

        async def fail_webhook():
            await all_started.wait()
            raise error

        self.webhook.side_effect = fail_webhook
        with self.assertRaises(RuntimeError) as raised:
            await local_main.run_local_app()
        self.assertIs(raised.exception, error)
        self.assertEqual(set(cleaned), {"api", "scheduler"})
        self.assertTrue(self.server.should_exit)

    async def test_unexpected_component_return_is_an_error(self):
        started, all_started, cleaned = set(), asyncio.Event(), []
        self._wait_for_components(started, all_started, cleaned)

        async def stop_scheduler():
            await all_started.wait()

        self.scheduler.side_effect = stop_scheduler
        with self.assertRaisesRegex(RuntimeError, "stopped unexpectedly"):
            await local_main.run_local_app()
        self.assertEqual(set(cleaned), {"api", "webhook"})

    async def test_server_requested_shutdown_stops_components(self):
        started, all_started, cleaned = set(), asyncio.Event(), []
        self._wait_for_components(started, all_started, cleaned)

        async def stop_server():
            await all_started.wait()
            self.server.should_exit = True

        self.server.serve.side_effect = stop_server
        await local_main.run_local_app()
        self.assertEqual(set(cleaned), {"scheduler", "webhook"})

    async def test_cancellation_cleans_up_all_components(self):
        started, all_started, cleaned = set(), asyncio.Event(), []
        self._wait_for_components(started, all_started, cleaned, expected_count=3)
        task = asyncio.create_task(local_main.run_local_app())
        await all_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(set(cleaned), {"api", "scheduler", "webhook"})


if __name__ == "__main__":
    unittest.main()
