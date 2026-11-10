import unittest
from unittest.mock import AsyncMock, patch

from collection_agents.notes.call_notes import (
    _extract_note_request_meta,
    persist_call_artifacts,
)


class CallFinalizationTests(unittest.IsolatedAsyncioTestCase):
    def test_copilot_call_uuid_falls_back_to_session_create(self):
        meta = _extract_note_request_meta(
            {"session_create": {"callUUID": "copilot-call-1"}}
        )
        self.assertEqual(meta["call_uuid"], "copilot-call-1")

    async def test_transcript_and_analysis_are_both_persisted(self):
        context = object()
        call_data = {"data": {"callUUID": "call-1"}}
        metrics_collector = object()
        save_conversation = AsyncMock()
        intelligent_notes_distribution = AsyncMock()

        with patch(
            "collection_agents.notes.call_notes.save_conversation",
            save_conversation,
        ), patch(
            "collection_agents.notes.call_notes.intelligent_notes_distribution",
            intelligent_notes_distribution,
        ):
            await persist_call_artifacts(
                context=context,
                call_data=call_data,
                call_uuid="call-1",
                locale="es-MX",
                metrics_collector=metrics_collector,
            )

        save_conversation.assert_awaited_once_with(
            context=context,
            call_uuid="call-1",
            conversation_meta={"language": "es-MX"},
            call_data=call_data,
            metrics_collector=metrics_collector,
        )
        intelligent_notes_distribution.assert_awaited_once_with(
            context=context,
            call_data=call_data,
            locale="es-MX",
        )

    async def test_analysis_failure_does_not_prevent_transcript_save(self):
        save_conversation = AsyncMock()
        intelligent_notes_distribution = AsyncMock(
            side_effect=RuntimeError("analysis unavailable")
        )

        with patch(
            "collection_agents.notes.call_notes.save_conversation",
            save_conversation,
        ), patch(
            "collection_agents.notes.call_notes.intelligent_notes_distribution",
            intelligent_notes_distribution,
        ):
            await persist_call_artifacts(
                context=object(),
                call_data={},
                call_uuid="call-1",
                locale="en-US",
            )

        save_conversation.assert_awaited_once()

    async def test_missing_context_is_skipped(self):
        save_conversation = AsyncMock()

        with patch(
            "collection_agents.notes.call_notes.save_conversation",
            save_conversation,
        ):
            await persist_call_artifacts(
                context=None,
                call_data={},
                call_uuid="call-1",
                locale="en-US",
            )

        save_conversation.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
