import asyncio
import json
import unittest
from types import SimpleNamespace

from fastcall_adapter.application.mock_call_media import MockCallMediaRuntime, _MockSession


class FakeWebSocket:
    def __init__(self):
        self.events = []
        self.closed = False

    async def send_text(self, value):
        self.events.append(json.loads(value))

    async def send_bytes(self, value):
        pass

    async def close(self):
        self.closed = True


class FakeGateway:
    def __init__(self):
        self.answers = []
        self.kills = []
        self.media = []

    async def answer_channel(self, uuid):
        self.answers.append(uuid)

    async def kill_channel(self, uuid, cause):
        self.kills.append((uuid, cause))

    async def start_mock_media(self, uuid, call_id):
        self.media.append((uuid, call_id))


class FakeTelephony:
    def bind_mock_browser(self, call_id, uuid):
        return SimpleNamespace(id=call_id, mock_browser_uuid=uuid)

    def get_mock_call(self, task_id):
        return None


class FakeUow:
    def __init__(self, call):
        self.telephony = FakeTelephony()
        self.calls = SimpleNamespace(get=lambda call_id: call)

    def commit(self):
        pass

    def close(self):
        pass


class MockCallMediaTests(unittest.TestCase):
    def test_incoming_answer_and_readiness_gate(self):
        async def scenario():
            call = SimpleNamespace(id="call-1", freeswitch_uuid="a-uuid")
            gateway = FakeGateway()
            runtime = MockCallMediaRuntime(lambda: FakeUow(call), gateway)
            browser = FakeWebSocket()
            session = runtime._sessions["task-1"] = _MockSession("task-1", browser)
            await runtime.handle_freeswitch({
                "Event-Name": "CHANNEL_CREATE", "Unique-ID": "b-uuid",
                "variable_fastcall_task_id": "task-1", "variable_fastcall_call_id": "call-1",
            })
            self.assertEqual(browser.events[-1]["event"], "incoming")
            self.assertEqual(gateway.answers, [])

            await runtime.answer("task-1")
            await runtime.answer("task-1")
            self.assertEqual(gateway.answers, ["b-uuid"])
            await runtime.handle_freeswitch({
                "Event-Name": "CHANNEL_ANSWER", "Unique-ID": "b-uuid",
                "variable_fastcall_task_id": "task-1", "variable_fastcall_call_id": "call-1",
            })
            self.assertEqual(gateway.media, [("b-uuid", "call-1")])

            waiting = asyncio.create_task(runtime.wait_agent_ready("call-1"))
            await runtime.a_media_bound("call-1")
            await asyncio.sleep(0)
            self.assertFalse(waiting.done())
            session.freeswitch = FakeWebSocket()
            runtime._update_ready(session)
            await asyncio.wait_for(waiting, timeout=1)
            await runtime.mark_active("call-1")
            self.assertEqual(browser.events[-1]["event"], "ready")
            await runtime.finish("call-1", "NORMAL_CLEARING")
            self.assertEqual(browser.events[-1]["result_code"], "NORMAL_CLEARING")

        asyncio.run(scenario())

    def test_dialing_hangup_without_call_ends_transport(self):
        async def scenario():
            gateway = FakeGateway()
            runtime = MockCallMediaRuntime(lambda: FakeUow(None), gateway)
            browser = FakeWebSocket()
            runtime._sessions["task-1"] = _MockSession("task-1", browser)
            await runtime.terminate("task-1")
            self.assertTrue(browser.closed)
            self.assertEqual(browser.events[-1]["event"], "ended")
            self.assertNotIn("task-1", runtime._sessions)
            self.assertFalse(runtime.has_browser("task-1"))
            self.assertEqual(gateway.kills, [])

        asyncio.run(scenario())

    def test_reject_kills_actual_a_and_b_channels(self):
        async def scenario():
            call = SimpleNamespace(
                id="call-1", freeswitch_uuid="a-uuid", origination_uuid="a-uuid",
                status="dialing", dial_requested_at=object(),
            )
            gateway = FakeGateway()
            runtime = MockCallMediaRuntime(lambda: FakeUow(call), gateway)
            browser = FakeWebSocket()
            session = runtime._sessions["task-1"] = _MockSession("task-1", browser)
            session.call_id = "call-1"
            session.browser_uuid = "b-uuid"
            runtime._by_call["call-1"] = session
            await runtime.terminate("task-1", "CALL_REJECTED")
            self.assertEqual(gateway.kills, [
                ("a-uuid", "CALL_REJECTED"), ("b-uuid", "CALL_REJECTED"),
            ])
            self.assertFalse(browser.closed)
            await runtime.finish("call-1", "CALL_REJECTED")
            self.assertTrue(browser.closed)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
