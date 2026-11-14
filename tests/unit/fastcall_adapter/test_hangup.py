import asyncio
import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastcall_adapter.application.hangup import ReconcileHangupsUseCase
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_core.infrastructure.orm import Base
from fastcall_core.infrastructure.outbound_orm import (
    CallEventModel,
    OutboundCallModel,
    OutboundTaskModel,
    WebhookDeliveryModel,
)
from fastcall.application.task_control import CancelTaskUseCase
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.fastcall.test_outbound_repositories import (
    NOW,
    contact_record,
    task_record,
)


class FakeHangupClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def hangup(self, call_id):
        self.calls.append(call_id)
        if self.fail:
            raise TimeoutError("adapter unavailable")


class FakeGateway:
    def __init__(self):
        self.killed = []

    async def kill_channel(self, channel_uuid, cause):
        self.killed.append((channel_uuid, cause))


class HangupFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.uow.tasks.add(task_record())
        self.uow.contacts.add(contact_record())
        self.uow.commit()
        self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        call = self.uow.session.get(OutboundCallModel, "call_1")
        call.status = "talking"
        call.freeswitch_uuid = "channel-1"
        self.uow.commit()

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def _cancel(self, client, hangup_active=True):
        task = self.uow.tasks.get("task_1")
        return asyncio.run(CancelTaskUseCase(
            self.uow, client, clock=lambda: NOW
        ).execute("task_1", task.state_version, hangup_active))

    def _instructions(self):
        return self.uow.session.query(CallEventModel).filter_by(
            event_type="call.hangup_requested"
        ).all()

    def test_cancel_persists_instruction_and_immediate_failure_is_not_cancel_failure(self):
        client = FakeHangupClient(fail=True)
        result = self._cancel(client)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(client.calls, ["call_1"])
        self.assertEqual(len(self._instructions()), 1)
        self.assertEqual(self.uow.event_worker.claim_due(NOW, "ordinary"), [])

    def test_cancel_without_hangup_does_not_create_instruction(self):
        client = FakeHangupClient()
        result = self._cancel(client, hangup_active=False)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(client.calls, [])
        self.assertEqual(self._instructions(), [])

    def test_reconciliation_waits_for_terminal_and_recovers_after_restart(self):
        self._cancel(FakeHangupClient(fail=True))
        gateway = FakeGateway()
        now = [NOW]
        first = ReconcileHangupsUseCase(
            lambda: SqlAlchemyAdapterUnitOfWork(self.sessions()),
            gateway, clock=lambda: now[0], owner="adapter-1",
        )
        self.assertEqual(
            asyncio.run(first.run_once()), [(self._instructions()[0].id, "waiting")]
        )
        self.assertEqual(gateway.killed, [("channel-1", "NORMAL_CLEARING")])

        now[0] = NOW + datetime.timedelta(seconds=2)
        call = self.uow.session.get(OutboundCallModel, "call_1")
        call.status = "cancelled"
        self.uow.commit()
        restarted = ReconcileHangupsUseCase(
            lambda: SqlAlchemyAdapterUnitOfWork(self.sessions()),
            gateway, clock=lambda: now[0], owner="adapter-2",
        )
        instruction_id = self._instructions()[0].id
        self.assertEqual(asyncio.run(restarted.run_once()), [(instruction_id, "processed")])
        self.assertEqual(len(gateway.killed), 1)
        self.assertEqual(self._instructions()[0].process_status, "processed")

    def test_cancel_finalizes_unauthorized_reservation_without_requeue(self):
        task = self.uow.session.get(OutboundTaskModel, "task_1")
        task.callback_url = "https://example.test/callback"
        call = self.uow.session.get(OutboundCallModel, "call_1")
        call.status = "reserved"
        call.dial_requested_at = None
        self.uow.commit()
        client = FakeHangupClient()
        result = self._cancel(client)
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(contact.status, "cancelled")
        self.assertIsNone(contact.active_call_id)
        self.assertEqual(contact.final_result_code, "CANCELLED")
        self.assertEqual(self.uow.calls.get("call_1").status, "cancelled")
        self.assertEqual(client.calls, [])
        self.assertEqual(self._instructions(), [])
        self.assertEqual(self.uow.session.query(WebhookDeliveryModel).filter_by(
            event_type="contact.completed"
        ).count(), 1)


if __name__ == "__main__":
    unittest.main()
