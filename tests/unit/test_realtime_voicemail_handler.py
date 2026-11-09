import unittest
from types import SimpleNamespace

from collection_agents.events import VoicemailDetectedEvent
from collection_agents.handlers.realtime_voicemail import RealtimeVoicemailHandler


class FakeContext:
    def __init__(self):
        self.messages = []

    def add_message(self, message):
        self.messages.append(message)


class FakeLLMService:
    def __init__(self):
        self.cancelled_events = []
        self.appended = []
        self.audio_input_paused = False

    def set_audio_input_paused(self, paused):
        self.audio_input_paused = paused

    async def cancel_current_response_for_barge_in(self, event):
        self.cancelled_events.append(event)

    async def append_context_message_and_create_response(
        self,
        context,
        message,
        *,
        priority=False,
        metadata=None,
    ):
        self.appended.append(
            {
                "context": context,
                "message": message,
                "priority": priority,
                "metadata": metadata,
            }
        )


class RealtimeVoicemailHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_detection_event_records_evidence_and_prompts_llm(self):
        session = {}
        context = FakeContext()
        hangup_state = SimpleNamespace(
            is_hanging_up=False,
            input_blocked=False,
        )
        llm_service = FakeLLMService()
        handler = RealtimeVoicemailHandler(
            session_context=session,
            context=context,
            hangup_state=hangup_state,
            llm_service=llm_service,
            locale="en-US",
            instant_close=False,
            on_hangup=lambda: None,
            on_end=lambda reason: None,
        )
        event = VoicemailDetectedEvent(
            transcript_text="Please leave a message after the tone",
            trigger_text="after the tone",
            item_id="item-1",
            source="realtime",
        )

        await handler.handle(event)

        self.assertTrue(session["voicemail_detected"])
        self.assertTrue(hangup_state.input_blocked)
        self.assertTrue(llm_service.audio_input_paused)
        self.assertEqual(context.messages[0]["role"], "user")
        self.assertIn("leave a message", context.messages[0]["content"])
        self.assertEqual(llm_service.cancelled_events[0]["text"], "after the tone")
        self.assertEqual(llm_service.appended[0]["metadata"]["reason"], "voicemail")
        self.assertEqual(llm_service.appended[0]["metadata"]["item_id"], "item-1")

    async def test_instant_close_bypasses_llm_response(self):
        ended = []
        hanged_up = []

        async def on_hangup():
            hanged_up.append(True)

        async def on_end(reason):
            ended.append(reason)

        handler = RealtimeVoicemailHandler(
            session_context={},
            context=FakeContext(),
            hangup_state=SimpleNamespace(
                is_hanging_up=False,
                input_blocked=False,
            ),
            llm_service=FakeLLMService(),
            locale="en-US",
            instant_close=True,
            on_hangup=on_hangup,
            on_end=on_end,
        )

        await handler.handle(
            VoicemailDetectedEvent(
                transcript_text="Please leave a message",
                source="realtime",
            )
        )

        self.assertEqual(hanged_up, [True])
        self.assertEqual(ended, ["voicemail_instant_close"])


if __name__ == "__main__":
    unittest.main()
