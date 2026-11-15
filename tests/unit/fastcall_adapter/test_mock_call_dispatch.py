import asyncio
import unittest

from fastcall_adapter.infrastructure.gateways.esl import FreeSwitchESL


class FakeCommands:
    def __init__(self):
        self.calls = []

    async def command(self, command):
        self.calls.append(command)
        return {"Reply-Text": "+OK Job-UUID: job-1"}


class MockCallESLTests(unittest.TestCase):
    def test_mock_originates_only_to_fixed_loopback_target(self):
        gateway = FreeSwitchESL(
            "localhost", 8021, "secret", "carrier", "ws://adapter/internal/media",
            dial_mode="gateway", mock_target="mock-call",
        )
        commands = FakeCommands()
        gateway.commands = commands
        asyncio.run(gateway.originate_mock("call_1", "origin_1", "task_1", "contact_1"))
        self.assertIn("loopback/mock-call &park()", commands.calls[0])
        self.assertIn("fastcall_mock_call=true", commands.calls[0])
        self.assertIn("STREAM_PLAYBACK_DIRECT=true", commands.calls[0])
        self.assertNotIn("sofia/gateway", commands.calls[0])

    def test_answer_and_mock_media_use_browser_leg(self):
        gateway = FreeSwitchESL(
            "localhost", 8021, "secret", "carrier",
            "ws://adapter/internal/media?token=legacy-secret", mock_target="mock-call",
            media_token="media-secret",
        )
        commands = FakeCommands()
        gateway.commands = commands
        asyncio.run(gateway.answer_channel("browser-uuid"))
        asyncio.run(gateway.start_mock_media("browser-uuid", "call-1"))
        self.assertIn("api uuid_answer browser-uuid", commands.calls[0])
        self.assertIn("STREAM_EXTRA_HEADERS", commands.calls[1])
        self.assertIn("browser-uuid start", commands.calls[2])
        self.assertIn("/call-1/browser ", commands.calls[2])
        self.assertNotIn("token=", commands.calls[2])


if __name__ == "__main__":
    unittest.main()
