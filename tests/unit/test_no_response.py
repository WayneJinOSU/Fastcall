import unittest

from collection_agents.handlers.common.no_response import NoResponseController


class ConfigManager:
    def __init__(self, hangup_enabled=True):
        self.hangup_enabled = hangup_enabled

    def get_no_response_config(self):
        return {
            "maxNoResponseCount": 2,
            "noResponseReply": "Are you there?",
            "noResponseHangUp": {
                "enabled": self.hangup_enabled,
                "hangupMessage": "Goodbye",
            },
        }


class NoResponseControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_then_farewell_and_fallback(self):
        replies = []
        farewells = []
        session = {"on_answer": True, "is_end": False, "no_response_count": 0}

        async def on_reply(text, current, maximum):
            replies.append(text)

        async def on_farewell(text, current, maximum):
            farewells.append(text)

        controller = NoResponseController(
            session_context=session,
            config_manager=ConfigManager(),
            is_hanging_up=lambda: False,
            on_reply=on_reply,
            on_farewell=on_farewell,
            on_hangup=lambda: None,
        )

        await controller.handle()
        await controller.handle()

        self.assertEqual(replies, ["Are you there?"])
        self.assertEqual(farewells, ["Goodbye"])
        self.assertTrue(session["no_response_hangup_queued"])
        session["no_response_hangup_task"].cancel()

    async def test_hangup_disabled_stops_without_fallback(self):
        session = {"on_answer": True, "is_end": False, "no_response_count": 1}
        controller = NoResponseController(
            session_context=session,
            config_manager=ConfigManager(hangup_enabled=False),
            is_hanging_up=lambda: False,
            on_reply=lambda *args: None,
            on_farewell=lambda *args: None,
            on_hangup=lambda: None,
        )
        await controller.handle()
        self.assertFalse(session.get("no_response_hangup_queued", False))
        self.assertIsNone(session.get("no_response_hangup_task"))

    async def test_voicemail_suppresses_no_response_actions(self):
        called = []
        session = {
            "on_answer": True,
            "is_end": False,
            "no_response_count": 0,
            "voicemail_detected": True,
        }
        controller = NoResponseController(
            session_context=session,
            config_manager=ConfigManager(),
            is_hanging_up=lambda: False,
            on_reply=lambda *args: called.append("reply"),
            on_farewell=lambda *args: called.append("farewell"),
            on_hangup=lambda: called.append("hangup"),
        )
        await controller.handle()
        self.assertEqual(called, [])
        self.assertEqual(session["no_response_count"], 0)


if __name__ == "__main__":
    unittest.main()
