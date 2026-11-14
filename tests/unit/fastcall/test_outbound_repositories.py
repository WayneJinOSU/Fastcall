import datetime
import unittest

from sqlalchemy import create_engine, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from fastcall_core.application.outbound_ports import (
    CallEventRecord,
    OutboundTaskRecord,
    TaskContactRecord,
    WebhookDeliveryRecord,
)
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.infrastructure.orm import Base
from fastcall_core.infrastructure.outbound_orm import (
    CallBlacklistModel,
    OutboundCallModel,
    OutboundTaskModel,
    SchedulerStateModel,
    TaskContactModel,
)
from fastcall_core.infrastructure.outbound_repositories import SqlAlchemyCallBlacklistRepository
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork


NOW = datetime.datetime(2026, 1, 1, 12, 0, 0)


def task_record(task_id="task_1", customer_task_id="customer_1"):
    return OutboundTaskRecord(
        id=task_id,
        customer_task_id=customer_task_id,
        name="Outbound",
        robot_app_id="app_1",
        robot_version_id="version_1",
        config_hash="sha256:test",
        status="running",
        health_status="healthy",
        timezone="Asia/Shanghai",
        default_country_code="86",
        scheduled_start_at=None,
        scheduled_end_at=None,
        calling_windows_json=[],
        excluded_dates_json=[],
        max_concurrent_calls=2,
        calls_per_minute=30,
        next_dial_at=None,
        last_dispatched_at=None,
        retry_policy_json={
            "max_retries": 1, "retry_intervals_sec": [60], "retry_on": ["NO_ANSWER"]
        },
        callback_url=None,
        callback_secret_ref=None,
        state_version=1,
        result_revision=0,
        last_error_code=None,
        created_at=NOW,
        started_at=NOW,
        completed_at=None,
        created_by="test",
    )


def contact_record(contact_id="contact_1", task_id="task_1", phone_hmac="a" * 64):
    return TaskContactRecord(
        id=contact_id,
        task_id=task_id,
        customer_contact_id="customer_contact_1",
        import_seq=1,
        phone_ciphertext=b"encrypted-phone",
        phone_last4="0001",
        phone_hmac=phone_hmac,
        hmac_key_version="v1",
        variables_ciphertext=b"encrypted-variables",
        encryption_key_version="v1",
        status="pending",
        retry_count=0,
        active_call_id=None,
        next_call_at=None,
        final_result_code=None,
        last_error_code=None,
        result_revision=0,
        state_version=1,
        created_at=NOW,
        updated_at=NOW,
        finished_at=None,
    )


class OutboundRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.uow.tasks.add(task_record())
        self.uow.contacts.add(contact_record())
        self.uow.commit()

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def _reserve_call(self):
        return self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)

    def test_status_transition_uses_expected_version_and_domain_rules(self):
        paused = self.uow.tasks.transition_status("task_1", "paused", 1, NOW)
        self.assertEqual(paused.status, "paused")
        self.assertEqual(paused.state_version, 2)
        self.uow.commit()
        with self.assertRaises(RobotConfigError) as error:
            self.uow.tasks.transition_status("task_1", "running", 1, NOW)
        self.assertEqual(error.exception.code, "STATE_VERSION_CONFLICT")
        self.uow.rollback()
        with self.assertRaises(RobotConfigError) as error:
            self.uow.tasks.transition_status("task_1", "scheduled", 2, NOW)
        self.assertEqual(error.exception.code, "INVALID_TASK_STATE_TRANSITION")

    def test_reservation_atomically_links_call_and_contact(self):
        call = self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(call.call_no, 1)
        self.assertEqual(call.status, "reserved")
        self.assertEqual(call.dial_phase, "prepared")
        self.assertIsNone(call.dial_requested_at)
        self.assertEqual(contact.status, "calling")
        self.assertEqual(contact.active_call_id, "call_1")
        self.assertEqual(contact.state_version, 2)
        self.assertEqual(self.uow.calls.count_active(), 1)
        self.assertEqual(self.uow.calls.count_active("task_1"), 1)
        with self.assertRaises(RobotConfigError):
            self.uow.calls.reserve("contact_1", "call_2", "origin_2", NOW, NOW)

    def test_only_unauthorized_reservation_can_be_requeued(self):
        self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        self.uow.calls.release_unauthorized_reservation("call_1", 1, NOW)
        call = self.uow.calls.get("call_1")
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(call.status, "cancelled")
        self.assertEqual(call.failure_code, "RESERVATION_EXPIRED")
        self.assertEqual(contact.status, "pending")
        self.assertIsNone(contact.active_call_id)
        self.assertEqual(contact.retry_count, 0)
        self.assertEqual(self.uow.calls.count_active(), 0)

        self.uow.calls.reserve("contact_1", "call_2", "origin_2", NOW, NOW)
        call_model = self.uow.session.query(OutboundCallModel).filter_by(id="call_2").one()
        call_model.dial_requested_at = NOW
        self.uow.session.flush()
        with self.assertRaises(RobotConfigError) as error:
            self.uow.calls.release_unauthorized_reservation("call_2", 1, NOW)
        self.assertEqual(error.exception.code, "RESERVATION_NOT_RELEASABLE")

    def test_reservation_release_cannot_revive_terminal_contact(self):
        self._reserve_call()
        self.uow.session.execute(
            update(TaskContactModel)
            .where(TaskContactModel.id == "contact_1")
            .values(status="failed")
        )
        with self.assertRaises(RobotConfigError) as error:
            self.uow.calls.release_unauthorized_reservation("call_1", 1, NOW)
        self.assertEqual(error.exception.code, "CONTACT_NOT_CALLING")
        self.assertEqual(self.uow.contacts.get("contact_1").status, "failed")

    def test_active_contact_requires_call_finalization_transaction(self):
        self._reserve_call()
        with self.assertRaises(RobotConfigError) as error:
            self.uow.contacts.transition_status("contact_1", "failed", 2, NOW)
        self.assertEqual(error.exception.code, "CALL_FINALIZATION_REQUIRED")

    def test_locked_read_refreshes_previously_loaded_task(self):
        self.uow.tasks.get("task_1")
        self.uow.session.execute(
            update(OutboundTaskModel)
            .where(OutboundTaskModel.id == "task_1")
            .values(status="paused", state_version=2)
        )
        self.assertEqual(self.uow.tasks.get_for_update("task_1").status, "paused")
        with self.assertRaises(RobotConfigError) as error:
            self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        self.assertEqual(error.exception.code, "OUTBOUND_TASK_NOT_RUNNING")

    def test_scheduler_state_is_limited_to_one_row(self):
        self.uow.session.add(SchedulerStateModel(id=2, next_dial_at=None))
        with self.assertRaises(IntegrityError):
            self.uow.session.flush()
        self.uow.rollback()

    def test_event_deduplication_rejects_same_id_with_different_hash(self):
        self._reserve_call()
        event = CallEventRecord(
            id="event_row_1", source="agent", event_id="agent:call_1:1:ready",
            event_type="session.ready", task_id="task_1", contact_id="contact_1",
            call_id="call_1", occurred_at=NOW, received_at=NOW, payload_json={"ready": True},
            payload_hash="a" * 64, process_status="pending", process_owner=None,
            process_lease_until=None, process_attempts=0, next_process_at=NOW,
            processed_at=None, last_error=None,
        )
        self.assertTrue(self.uow.call_events.enqueue(event))
        event.id = "event_row_2"
        self.assertFalse(self.uow.call_events.enqueue(event))
        event.id = "event_row_3"
        event.payload_hash = "b" * 64
        with self.assertRaises(RobotConfigError) as error:
            self.uow.call_events.enqueue(event)
        self.assertEqual(error.exception.code, "EVENT_ID_CONFLICT")
        self.assertTrue(self.uow.call_events.has_event("call_1", "session.ready"))

    def test_webhook_deduplication_keeps_original_payload(self):
        self._reserve_call()
        delivery = WebhookDeliveryRecord(
            id="delivery_1", event_id="contact:contact_1:1", aggregate_type="contact",
            aggregate_id="contact_1", aggregate_revision=1, task_id="task_1",
            contact_id="contact_1", call_id="call_1", event_type="contact.completed",
            endpoint_url="https://example.test/callback", payload_json={"status": "failed"},
            status="pending", delivery_owner=None, delivery_lease_until=None,
            attempt_count=0, next_delivery_at=NOW, last_http_status=None,
            last_error=None, delivered_at=None, created_at=NOW,
        )
        self.assertTrue(self.uow.webhook_deliveries.enqueue(delivery))
        delivery.id = "delivery_2"
        self.assertFalse(self.uow.webhook_deliveries.enqueue(delivery))
        delivery.id = "delivery_3"
        delivery.payload_json = {"status": "succeeded"}
        with self.assertRaises(RobotConfigError):
            self.uow.webhook_deliveries.enqueue(delivery)

    def test_duplicate_phone_is_rejected(self):
        duplicate = contact_record(
            contact_id="contact_2", phone_hmac="a" * 64
        )
        duplicate.customer_contact_id = "customer_contact_2"
        duplicate.import_seq = 2
        with self.assertRaises(IntegrityError):
            self.uow.contacts.add(duplicate)
        self.uow.rollback()

    def test_blacklist_checks_all_hmac_versions_and_expiry(self):
        from fastcall_core.infrastructure.outbound_orm import CallBlacklistModel

        self.uow.session.add(CallBlacklistModel(
            id="blacklist_1", phone_ciphertext=b"encrypted", encryption_key_version="v1",
            phone_hmac="a" * 64, hmac_key_version="v2", source="import", reason=None,
            effective_at=NOW, expires_at=NOW + datetime.timedelta(minutes=1),
            created_by="test", created_at=NOW,
        ))
        self.uow.session.flush()
        repository = SqlAlchemyCallBlacklistRepository(self.uow.session)
        self.assertTrue(repository.is_blocked([("a" * 64, "v1"), ("a" * 64, "v2")], NOW))
        self.assertFalse(repository.is_blocked([("a" * 64, "v1")], NOW))
        self.assertFalse(repository.is_blocked(
            [("a" * 64, "v2")], NOW + datetime.timedelta(minutes=2)
        ))
        with self.assertRaises(RobotConfigError):
            repository.is_blocked([], NOW)


if __name__ == "__main__":
    unittest.main()
