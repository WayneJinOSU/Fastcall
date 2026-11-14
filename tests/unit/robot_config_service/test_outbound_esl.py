import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from robot_config_service.infrastructure.freeswitch.esl import ESLConnection, FreeSwitchESL


class FakeCommands:
    def __init__(self):
        self.calls = []

    async def command(self, value):
        self.calls.append(value)
        return {"Reply-Text": "+OK Job-UUID: job-1"}


class FreeSwitchESLTests(unittest.TestCase):
    def test_loopback_mode_uses_loopback_destination(self):
        gateway = FreeSwitchESL(
            "localhost", 8021, "secret", "carrier", "ws://adapter/media",
            dial_mode="loopback",
        )
        commands = FakeCommands()
        gateway.commands = commands
        asyncio.run(gateway.originate("call_1", "origin_1", "+12025550101", "task_1", "contact_1"))
        self.assertIn("loopback/12025550101 &park()", commands.calls[0])
        self.assertNotIn("sofia/gateway", commands.calls[0])

    def test_media_path_is_built_before_query(self):
        gateway = FreeSwitchESL(
            "localhost", 8021, "secret", "carrier",
            "ws://adapter/internal/media?token=secret", dial_mode="loopback",
        )
        commands = FakeCommands()
        gateway.commands = commands
        asyncio.run(gateway.start_media("channel-1", "call-1", 1))
        self.assertIn("/internal/media/call-1?token=secret", commands.calls[0])
        self.assertNotIn("?token=secret/call-1", commands.calls[0])

    def test_api_response_body_is_normalized(self):
        connection = ESLConnection("localhost", 8021, "secret")
        connection.writer = Mock()
        connection.writer.drain = AsyncMock()
        connection.read_message = AsyncMock(return_value={"_body": "true"})
        response = asyncio.run(connection.command("api uuid_exists channel-1"))
        self.assertEqual(response["Reply-Text"], "true")

    def test_stop_media_is_idempotent_when_channel_has_ended(self):
        gateway = FreeSwitchESL("localhost", 8021, "secret", "carrier", "ws://adapter/media")
        gateway.commands.command = AsyncMock(return_value={"Reply-Text": "-ERR Operation Failed"})
        gateway.channel_exists = AsyncMock(return_value=False)
        asyncio.run(gateway.stop_media("ended-channel"))
        gateway.channel_exists.assert_awaited_once_with("ended-channel")

    def test_event_plain_body_headers_are_parsed(self):
        connection = ESLConnection("localhost", 8021, "secret")
        body = "Event-Name: CHANNEL_ANSWER\nUnique-ID: origin-1\n"
        lines = [
            b"Content-Type: text/event-plain\r\n",
            f"Content-Length: {len(body.encode())}\r\n".encode(),
            b"\r\n",
        ]
        connection.reader = Mock()
        connection.reader.readline = AsyncMock(side_effect=lines)
        connection.reader.readexactly = AsyncMock(return_value=body.encode())
        message = asyncio.run(connection.read_message())
        self.assertEqual(message["Event-Name"], "CHANNEL_ANSWER")
        self.assertEqual(message["Unique-ID"], "origin-1")

    def test_default_mode_keeps_sip_gateway_behavior(self):
        gateway = FreeSwitchESL("localhost", 8021, "secret", "carrier", "ws://adapter/media")
        commands = FakeCommands()
        gateway.commands = commands
        asyncio.run(gateway.originate("call_1", "origin_1", "+12025550101", "task_1", "contact_1"))
        self.assertIn("sofia/gateway/carrier/+12025550101 &park()", commands.calls[0])
        self.assertIn("STREAM_PLAYBACK=true", commands.calls[0])
        self.assertIn("STREAM_BUFFER_SIZE=20", commands.calls[0])


if __name__ == "__main__":
    unittest.main()
