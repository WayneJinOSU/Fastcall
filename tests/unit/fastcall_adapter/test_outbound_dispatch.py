import asyncio
import datetime
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall_adapter.api.app import create_app
from fastcall_adapter.application.call_checks import CallCheckUseCase
from fastcall_core.application.call_events import AgentSessionUseCase
from fastcall_adapter.application.dispatch import DispatchService
from fastcall_adapter.application.telephony_events import FreeSwitchEventService
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_adapter.settings import Settings, result_changed_events_enabled
from fastcall_core.infrastructure.orm import Base, RobotAppModel, RobotAppVersionModel
from fastcall_core.infrastructure.crypto import PhoneHmac, SecretCodec
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall_core.domain.errors import RobotConfigError
from tests.unit.fastcall.fixtures import valid_config
from tests.unit.fastcall.test_outbound_repositories import NOW, contact_record, task_record


class FakeGateway:
    def __init__(self, fail=False):
        self.fail = fail
        self.originate_calls = []

    async def health_check(self):
        return True

    async def channel_exists(self, channel_uuid):
        return False

    async def originate(self, **kwargs):
        self.originate_calls.append(kwargs)
        if self.fail:
            raise TimeoutError("originate timeout")
        return {"uuid": kwargs["origination_uuid"], "job_uuid": "job-1"}


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.codec = SecretCodec({"v1": b"k" * 32}, "v1")
        self.hmac = PhoneHmac({"v1": b"h" * 32}, "v1")
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self._create_robot()
        task = task_record()
        self.uow.tasks.add(task)
        contact = contact_record()
        contact.phone_ciphertext = self.codec.encrypt("+15550000001")
        contact.variables_ciphertext = self.codec.encrypt("{}")
        self.uow.contacts.add(contact)
        self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        self.uow.commit()
        self.uow.close()
        self.uow = SqlAlchemyAdapterUnitOfWork(
            self.sessions(), result_changed_enabled=result_changed_events_enabled
        )

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def _create_robot(self):
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

    def _service(self, gateway):
        return DispatchService(
            lambda: SqlAlchemyAdapterUnitOfWork(
                self.sessions(), result_changed_enabled=result_changed_events_enabled
            ), gateway, self.codec, self.hmac,
            clock=lambda: NOW, adapter_boot_id="boot-1",
        )

    def _move_call_to_unknown(self):
        from fastcall_core.infrastructure.outbound_orm import OutboundCallModel, OutboundTaskModel

        async def scenario():
            await self._service(FakeGateway(fail=True)).dispatch("call_1")

        asyncio.run(scenario())
        self.uow.session.get(OutboundTaskModel, "task_1").callback_url = "https://example.test/callback"
        call = self.uow.session.get(OutboundCallModel, "call_1")
        call.check_deadline_at = NOW - datetime.timedelta(seconds=1)
        self.uow.commit()
        CallCheckUseCase(self.uow).expire_checks()

    def _revise_unknown_with_late_terminal(self):
        self._move_call_to_unknown()
        FreeSwitchEventService(self.uow, clock=lambda: NOW).handle(
            instance_id="fs-1", stream_epoch="boot-1", channel_uuid="origin_1",
            origination_uuid="origin_1", fastcall_call_id="call_1", occurred_at=NOW,
            event_type="CHANNEL_DESTROY", event_sequence=2, raw_hangup_cause="NO_ANSWER",
        )

    def test_duplicate_dispatch_and_restart_never_resend_originate(self):
        async def scenario():
            gateway = FakeGateway()
            service = self._service(gateway)
            first = await service.dispatch("call_1")
            second = await service.dispatch("call_1")
            self.assertEqual(first["dispatch"], "started")
            self.assertEqual(second["dispatch"], "already_started")
            restarted = self._service(gateway)
            restarted.adapter_boot_id = "boot-2"
            recovered = await restarted.dispatch("call_1")
            self.assertEqual(recovered["dispatch"], "checking")
            self.assertEqual(len(gateway.originate_calls), 1)
            call = self.uow.calls.get("call_1")
            self.assertEqual(call.dial_requested_at, NOW)
            self.assertIsNotNone(call.dial_acknowledged_at)

        asyncio.run(scenario())

    def test_originate_timeout_enters_checking_without_retry(self):
        async def scenario():
            gateway = FakeGateway(fail=True)
            result = await self._service(gateway).dispatch("call_1")
            self.assertEqual(result["dispatch"], "checking")
            self.assertEqual(len(gateway.originate_calls), 1)
            call = self.uow.calls.get("call_1")
            self.assertEqual(call.status, "checking")
            self.assertEqual(call.result_code, "ORIGINATE_RESULT_UNKNOWN")
            self.assertEqual(self.uow.contacts.get("contact_1").retry_count, 0)

        asyncio.run(scenario())

    def test_blacklist_final_gate_blocks_originate(self):
        from fastcall_core.infrastructure.outbound_orm import CallBlacklistModel
        self.uow.session.add(CallBlacklistModel(
            id="block_1", phone_ciphertext=b"secret", encryption_key_version="v1",
            phone_hmac=self.hmac.sign("+15550000001"), hmac_key_version="v1",
            source="test", reason=None, effective_at=NOW, expires_at=None,
            created_by="test", created_at=NOW,
        ))
        self.uow.commit()

        async def scenario():
            gateway = FakeGateway()
            with self.assertRaises(RobotConfigError):
                await self._service(gateway).dispatch("call_1")
            self.assertEqual(gateway.originate_calls, [])
            self.assertEqual(self.uow.contacts.get("contact_1").status, "failed")
            self.assertEqual(self.uow.contacts.get("contact_1").retry_count, 0)

        asyncio.run(scenario())

    def test_telephony_events_answer_and_hangup_before_ready(self):
        async def scenario():
            await self._service(FakeGateway()).dispatch("call_1")

        asyncio.run(scenario())
        service = FreeSwitchEventService(self.uow, clock=lambda: NOW)
        common = {
            "instance_id": "fs-1", "stream_epoch": "boot-1", "channel_uuid": "origin_1",
            "origination_uuid": "origin_1", "fastcall_call_id": "call_1", "occurred_at": NOW,
        }
        service.handle(**common, event_type="CHANNEL_PROGRESS", event_sequence=1)
        service.handle(**common, event_type="CHANNEL_ANSWER", event_sequence=2)
        self.assertEqual(self.uow.calls.get("call_1").status, "answered")
        service.handle(
            **common, event_type="CHANNEL_DESTROY", event_sequence=3,
            raw_hangup_cause="NORMAL_CLEARING",
        )
        call = self.uow.calls.get("call_1")
        self.assertEqual(call.status, "failed")
        self.assertEqual(call.result_code, "AGENT_READY_TIMEOUT")
        self.assertEqual(self.uow.contacts.get("contact_1").retry_count, 0)

    def test_no_answer_schedules_retry_and_destroy_is_idempotent(self):
        from fastcall_core.infrastructure.outbound_orm import OutboundTaskModel, WebhookDeliveryModel
        self.uow.session.get(OutboundTaskModel, "task_1").callback_url = "https://example.test/callback"
        self.uow.commit()

        async def scenario():
            await self._service(FakeGateway()).dispatch("call_1")

        asyncio.run(scenario())
        service = FreeSwitchEventService(self.uow, clock=lambda: NOW)
        arguments = {
            "instance_id": "fs-1", "stream_epoch": "boot-1", "channel_uuid": "origin_1",
            "origination_uuid": "origin_1", "fastcall_call_id": "call_1", "occurred_at": NOW,
            "event_type": "CHANNEL_DESTROY", "event_sequence": 4,
            "raw_hangup_cause": "NO_ANSWER",
        }
        service.handle(**arguments)
        duplicate = service.handle(**arguments)
        self.assertEqual(duplicate["ack"], "duplicate")
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(contact.status, "waiting_retry")
        self.assertEqual(contact.retry_count, 1)
        self.assertEqual(contact.next_call_at, NOW + datetime.timedelta(seconds=60))
        self.assertEqual(self.uow.results.call_history("contact_1")["call_count"], 1)
        self.assertEqual(self.uow.session.query(WebhookDeliveryModel).count(), 0)

    def test_check_timeout_and_manual_revision_keep_capacity_until_review(self):
        from fastcall_core.infrastructure.outbound_orm import OutboundCallModel, OutboundTaskModel

        async def scenario():
            gateway = FakeGateway(fail=True)
            await self._service(gateway).dispatch("call_1")

        asyncio.run(scenario())
        task_model = self.uow.session.get(OutboundTaskModel, "task_1")
        task_model.callback_url = "https://example.test/callback"
        call_model = self.uow.session.get(OutboundCallModel, "call_1")
        call_model.check_deadline_at = NOW - datetime.timedelta(seconds=1)
        self.uow.commit()
        self.assertEqual(CallCheckUseCase(self.uow).expire_checks(), ["call_1"])
        call = self.uow.calls.get("call_1")
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(call.status, "unknown")
        self.assertEqual(contact.status, "unknown")
        self.assertEqual(contact.active_call_id, "call_1")
        self.assertEqual(self.uow.calls.count_active(), 1)
        self.assertEqual(self.uow.tasks.get("task_1").status, "completed")
        with self.assertRaises(RobotConfigError) as error:
            asyncio.run(CallCheckUseCase(self.uow, None).review("call_1", {
                "expected_state_version": call.state_version, "resolution": "failed",
                "result_code": "CONFIRMED_NOT_DIALED", "evidence_type": "CDR",
                "evidence_ref": "cdr-0", "note": "unsafe without gateway", "force_release": False,
            }, "reviewer"))
        self.assertEqual(error.exception.code, "CALL_REVIEW_UNAVAILABLE")
        self.assertEqual(self.uow.calls.get("call_1").status, "unknown")
        request = {
            "expected_state_version": call.state_version, "resolution": "succeeded",
            "result_code": "USER_HANGUP", "evidence_type": "CDR_AND_CHANNEL_SCAN",
            "evidence_ref": "cdr-1", "note": "Verified", "force_release": False,
        }
        asyncio.run(CallCheckUseCase(self.uow, FakeGateway()).review("call_1", request, "reviewer"))
        self.assertEqual(self.uow.calls.get("call_1").status, "succeeded")
        self.assertEqual(self.uow.contacts.get("contact_1").result_revision, 2)
        self.assertEqual(self.uow.tasks.get("task_1").status, "completed")
        self.assertEqual(self.uow.calls.count_active(), 0)

    def test_second_terminal_channel_event_is_absorbed(self):
        async def scenario():
            await self._service(FakeGateway()).dispatch("call_1")

        asyncio.run(scenario())
        service = FreeSwitchEventService(self.uow, clock=lambda: NOW)
        common = {
            "instance_id": "fs-1", "stream_epoch": "boot-1", "channel_uuid": "origin_1",
            "origination_uuid": "origin_1", "fastcall_call_id": "call_1", "occurred_at": NOW,
            "raw_hangup_cause": "NO_ANSWER",
        }
        service.handle(**common, event_type="CHANNEL_DESTROY", event_sequence=1)
        result = service.handle(**common, event_type="CHANNEL_HANGUP_COMPLETE", event_sequence=2)
        self.assertEqual(result["ack"], "terminal_duplicate")
        self.assertEqual(self.uow.contacts.get("contact_1").retry_count, 1)

    def test_cancelled_task_does_not_enter_waiting_retry(self):
        async def scenario():
            await self._service(FakeGateway()).dispatch("call_1")

        asyncio.run(scenario())
        task = self.uow.tasks.get("task_1")
        self.uow.tasks.transition_status("task_1", "cancelled", task.state_version, NOW)
        self.uow.commit()
        FreeSwitchEventService(self.uow, clock=lambda: NOW).handle(
            instance_id="fs-1", stream_epoch="boot-1", channel_uuid="origin_1",
            origination_uuid="origin_1", fastcall_call_id="call_1", occurred_at=NOW,
            event_type="CHANNEL_DESTROY", event_sequence=1, raw_hangup_cause="NO_ANSWER",
        )
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(contact.status, "failed")
        self.assertEqual(contact.retry_count, 0)

    def _review_unknown_call(self):
        call = self.uow.calls.get("call_1")
        request = {
            "expected_state_version": call.state_version, "resolution": "succeeded",
            "result_code": "USER_HANGUP", "evidence_type": "CDR_AND_CHANNEL_SCAN",
            "evidence_ref": "cdr-1", "note": "Verified", "force_release": False,
        }
        asyncio.run(CallCheckUseCase(self.uow, FakeGateway()).review(
            "call_1", request, "reviewer"
        ))

    def _result_changed_count(self):
        from fastcall_core.infrastructure.outbound_orm import WebhookDeliveryModel

        return self.uow.session.query(WebhookDeliveryModel).filter_by(
            event_type="contact.result_changed"
        ).count()

    def test_late_terminal_revision_does_not_emit_result_changed_by_default(self):
        with patch.dict(os.environ) as environment:
            environment.pop("FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED", None)
            self._revise_unknown_with_late_terminal()
        self.assertEqual(self.uow.contacts.get("contact_1").status, "failed")
        self.assertEqual(self.uow.contacts.get("contact_1").result_revision, 2)
        self.assertEqual(self._result_changed_count(), 0)

    def test_late_terminal_revision_emits_result_changed_when_enabled(self):
        with patch.dict(os.environ, {"FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED": "true"}):
            self._revise_unknown_with_late_terminal()
        self.assertEqual(self._result_changed_count(), 1)

    def test_manual_revision_does_not_emit_result_changed_by_default(self):
        self._move_call_to_unknown()
        with patch.dict(os.environ) as environment:
            environment.pop("FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED", None)
            self._review_unknown_call()
        self.assertEqual(self.uow.contacts.get("contact_1").result_revision, 2)
        self.assertEqual(self._result_changed_count(), 0)

    def test_manual_revision_emits_result_changed_when_enabled(self):
        self._move_call_to_unknown()
        with patch.dict(os.environ, {"FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED": "true"}):
            self._review_unknown_call()
        self.assertEqual(self.uow.contacts.get("contact_1").result_revision, 2)
        self.assertEqual(self._result_changed_count(), 1)

    def test_review_route_executes_adapter_transaction(self):
        self._move_call_to_unknown()
        call = self.uow.calls.get("call_1")
        settings = Settings(
            "test", "mysql+pymysql://unused:unused@localhost/unused",
            admin_token="admin-token", adapter_token="adapter-token",
            agent_token="agent-token", media_token="media-token",
        )
        app = create_app(
            settings=settings,
            uow_factory=lambda: SqlAlchemyAdapterUnitOfWork(
                self.sessions(), result_changed_enabled=result_changed_events_enabled
            ),
            secret_codec=self.codec, phone_hmac=self.hmac,
            freeswitch_gateway=FakeGateway(), validate_startup=False,
        )
        response = TestClient(app).post(
            "/adminapi/v1/calls/call_1/review",
            headers={"Authorization": "Bearer admin-token", "X-Actor-Id": "reviewer"},
            json={
                "expected_state_version": call.state_version, "resolution": "succeeded",
                "result_code": "USER_HANGUP", "evidence_type": "CDR_AND_CHANNEL_SCAN",
                "evidence_ref": "cdr-1", "note": "Verified", "force_release": False,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "succeeded")
        self.assertEqual(self.uow.contacts.get("contact_1").result_revision, 2)


if __name__ == "__main__":
    unittest.main()
