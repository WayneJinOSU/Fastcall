import asyncio
import datetime
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from collection_agents.session_registry import SessionSlotError, SessionSlotRegistry
from fastcall_core.application.call_events import AgentSessionUseCase
from fastcall_adapter.application.media import AdapterMediaRuntime
from fastcall_adapter.application.telephony_events import FreeSwitchEventService
from fastcall_core.infrastructure.orm import Base, RobotAppModel, RobotAppVersionModel
from fastcall_core.infrastructure.outbound_orm import (
    CallAnalysisModel,
    OutboundCallModel,
    WebhookDeliveryModel,
)
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_core.infrastructure.crypto import SecretCodec
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.fastcall.fixtures import valid_config
from tests.unit.fastcall.test_outbound_repositories import NOW, contact_record, task_record


class AgentSessionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.codec = SecretCodec({"v1": b"k" * 32}, "v1")
        self.uow.session.add(RobotAppModel(
            id="app_1", app_code="app", name="App", enabled=True, draft_json={},
            draft_revision=1, created_at=NOW, updated_at=NOW,
        ))
        self.uow.session.add(RobotAppVersionModel(
            id="version_1", app_id="app_1", version_no=1, source_draft_revision=1,
            content_json=valid_config(), config_hash="sha256:test", schema_version="robot-app/v1",
            runtime_engine="standard", runtime_adapter="deepseek_chat",
            runtime_catalog_version="test", validation_report_json={}, validated_at=NOW,
            created_by="test", created_at=NOW,
        ))
        task = task_record()
        task.callback_url = "https://example.test/callback"
        self.uow.tasks.add(task)
        contact = contact_record()
        contact.phone_ciphertext = self.codec.encrypt("+15550000001")
        contact.variables_ciphertext = self.codec.encrypt('{"user_name":"Alice"}')
        self.uow.contacts.add(contact)
        self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        call = self.uow.session.get(OutboundCallModel, "call_1")
        call.status = "answered"
        call.answered_at = NOW
        self.uow.commit()
        self.uow.close()
        self.uow = SqlAlchemyAdapterUnitOfWork(self.sessions())
        self.use_case = AgentSessionUseCase(self.uow, self.codec, clock=lambda: NOW)

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def _event(self, action, generation=1, detail=None):
        occurred = NOW + datetime.timedelta(seconds=1)
        return self.use_case.handle(
            "call_1", action, generation, "agent-test", occurred,
            f"agent:call_1:{generation}:{action}", detail,
        )

    def test_repeated_claim_and_attach_do_not_advance_generation(self):
        first = self.use_case.claim("call_1")
        second = self.use_case.claim("call_1")
        self.assertEqual(first["claim_generation"], 1)
        self.assertEqual(second["claim_generation"], 1)
        self.assertEqual(first["start_payload"]["data"]["callee"], "+15550000001")
        self.assertEqual(first["start_payload"]["data"]["variables"], {"user_name": "Alice"})
        self.assertEqual(first["start_payload"]["data"]["config"], valid_config())
        self.assertEqual(self._event("attached")["ack"], "processed")
        self.assertEqual(self._event("attached")["ack"], "duplicate")
        self.assertEqual(self.uow.calls.get("call_1").session_state, "attached")
        self._event("ready")
        call = self.uow.calls.get("call_1")
        self.assertEqual(call.status, "talking")
        self.assertIsNotNone(call.session_ready_at)
        with self.assertRaises(Exception):
            self.use_case.claim("call_1", recover=True)

    def test_pre_ready_recovery_rejects_stale_ready_and_failed_blocks_recovery(self):
        self.use_case.claim("call_1")
        self._event("attached")
        recovered = self.use_case.claim("call_1", recover=True)
        self.assertEqual(recovered["claim_generation"], 2)
        with self.assertRaises(Exception):
            self._event("ready", generation=1)
        self._event("failed", generation=1, detail={"message": "provider unavailable"})
        call = self.uow.calls.get("call_1")
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(call.status, "failed")
        self.assertEqual(call.session_state, "failed")
        self.assertEqual(contact.status, "failed")
        self.assertIsNone(contact.active_call_id)
        self.assertEqual(contact.result_revision, 1)
        self.assertEqual(self.uow.session.query(WebhookDeliveryModel).count(), 2)
        with self.assertRaises(Exception):
            self.use_case.claim("call_1", recover=True)
        self.assertEqual(self._event(
            "failed", generation=1, detail={"message": "provider unavailable"}
        )["ack"], "duplicate")

    def test_ready_rejects_old_generation_and_post_ready_failed_requires_current(self):
        self.use_case.claim("call_1")
        self._event("attached")
        self.use_case.claim("call_1", recover=True)
        with self.assertRaises(Exception):
            self._event("ready", generation=1)
        self._event("attached", generation=2)
        self._event("ready", generation=2)
        with self.assertRaises(Exception):
            self._event("failed", generation=1)
        self._event("failed", generation=2)
        self.assertEqual(self.uow.calls.get("call_1").result_code, "AI_PROVIDER_FAILED")

    def test_voicemail_completion_is_success_and_not_human(self):
        self.use_case.claim("call_1")
        self._event("attached")
        self._event("ready")
        self._event("completed", detail={
            "voicemail": True, "business_result": "NOT_INTERESTED",
            "analysis": {"intent": "not_interested"},
        })
        call = self.uow.calls.get("call_1")
        self.assertEqual(call.status, "succeeded")
        self.assertEqual(call.result_code, "VOICEMAIL")
        self.assertFalse(call.answered_by_human)
        analysis = self.uow.session.query(CallAnalysisModel).one()
        self.assertEqual(analysis.business_result, "NOT_INTERESTED")

    def test_completed_after_freeswitch_terminal_saves_analysis_and_acks(self):
        self.use_case.claim("call_1")
        self._event("attached")
        self._event("ready")
        FreeSwitchEventService(self.uow, clock=lambda: NOW).handle(
            instance_id="fs-1", stream_epoch="boot-1", channel_uuid="origin_1",
            origination_uuid="origin_1", fastcall_call_id="call_1", occurred_at=NOW,
            event_type="CHANNEL_DESTROY", event_sequence=1,
            raw_hangup_cause="NORMAL_CLEARING",
        )
        response = self._event("completed", detail={
            "business_result": "PROMISED", "analysis": {"intent": "promised"},
        })
        self.assertEqual(response["ack"], "terminal_duplicate")
        self.assertEqual(
            self.uow.session.query(CallAnalysisModel).one().business_result, "PROMISED"
        )

    def test_pre_ready_recovery_uses_new_generation(self):
        async def scenario():
            self.use_case.claim("call_1")
            runtime = AdapterMediaRuntime(lambda: self.uow, self.codec, None, "ws://unused", "token")
            with patch.object(runtime, "run_call", new_callable=AsyncMock) as run_call:
                await runtime.recover_sessions()
                await asyncio.sleep(0)
                run_call.assert_called_once_with("call_1", recover=True)

        asyncio.run(scenario())


class SessionRegistryTests(unittest.TestCase):
    def test_slot_is_held_until_cleanup_and_old_generations_are_rejected(self):
        async def scenario():
            registry = SessionSlotRegistry()
            slot = await registry.attach("call_1", 1)
            with self.assertRaises(SessionSlotError):
                await registry.attach("call_1", 1)
            with self.assertRaises(SessionSlotError):
                await registry.attach("call_1", 2)
            await registry.release(slot)
            second = await registry.attach("call_1", 2)
            with self.assertRaises(SessionSlotError):
                await registry.attach("call_1", 1)
            await registry.release(second)
            self.assertFalse(await registry.has_slot("call_1"))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
