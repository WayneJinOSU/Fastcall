import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastcall_adapter.infrastructure.gateways.esl import ESLError, ESLConnection, FreeSwitchESL


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

    def test_command_timeout_resets_connection_before_retry(self):
        async def scenario():
            connection = ESLConnection("localhost", 8021, "secret", command_timeout=0.01)
            writer = Mock()
            writer.drain = AsyncMock()
            writer.wait_closed = AsyncMock()
            connection.writer = writer

            async def never_respond():
                await asyncio.Event().wait()

            connection.read_message = AsyncMock(side_effect=never_respond)
            with self.assertRaises(asyncio.TimeoutError):
                await connection.command("api status")
            writer.close.assert_called_once()
            self.assertIsNone(connection.writer)

            connection.writer = None
            connection.read_message = AsyncMock(return_value={"Reply-Text": "+OK"})

            async def reconnect():
                connection.writer = writer

            connection.connect = AsyncMock(side_effect=reconnect)
            response = await connection.command("api status")
            self.assertEqual(response["Reply-Text"], "+OK")
            connection.connect.assert_awaited_once()

        asyncio.run(scenario())

    def test_protocol_failure_also_resets_connection(self):
        async def scenario():
            connection = ESLConnection("localhost", 8021, "secret")
            writer = Mock()
            writer.drain = AsyncMock()
            writer.wait_closed = AsyncMock()
            connection.writer = writer
            connection.read_message = AsyncMock(side_effect=ESLError("connection closed"))
            with self.assertRaises(ESLError):
                await connection.command("api status")
            writer.close.assert_called_once()
            self.assertIsNone(connection.writer)

        asyncio.run(scenario())

    def test_event_listener_closes_connection_when_cancelled(self):
        async def scenario():
            events = ESLConnection("localhost", 8021, "secret")
            events.connect = AsyncMock()
            events.command = AsyncMock()
            events.close = AsyncMock()
            reading = asyncio.Event()

            async def wait_for_event():
                reading.set()
                await asyncio.Event().wait()

            events.read_message = AsyncMock(side_effect=wait_for_event)
            with patch(
                "fastcall_adapter.infrastructure.gateways.esl.ESLConnection",
                return_value=events,
            ):
                gateway = FreeSwitchESL("localhost", 8021, "secret", "carrier", "ws://adapter/media")
                task = asyncio.create_task(gateway.listen_events(AsyncMock()))
                await reading.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            events.close.assert_awaited_once()

        asyncio.run(scenario())

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
