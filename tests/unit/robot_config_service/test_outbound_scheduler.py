import asyncio
import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from robot_config_service.application.scheduler import DialScheduler
from robot_config_service.domain.calling_windows import (
    is_within_calling_window,
    next_calling_window,
    validate_calling_windows,
    window_bounds,
)
from robot_config_service.infrastructure.orm import Base, RobotAppModel, RobotAppVersionModel
from robot_config_service.infrastructure.outbound_orm import TaskContactModel
from robot_config_service.infrastructure.security import PhoneHmac, SecretCodec
from robot_config_service.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from tests.unit.robot_config_service.fixtures import valid_config
from tests.unit.robot_config_service.test_outbound_repositories import NOW, contact_record, task_record
from zoneinfo import ZoneInfo


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    async def dispatch(self, call_id):
        self.calls.append(call_id)
        return {"dispatch": "started"}


class CallingWindowTests(unittest.TestCase):
    def test_overnight_overlap_and_excluded_start_date(self):
        windows = [
            {"days": [5], "start": "22:00", "end": "02:00"},
            {"days": [6], "start": "01:00", "end": "03:00"},
        ]
        zone = ZoneInfo("Asia/Shanghai")
        friday = datetime.datetime(2026, 1, 2, 23, 0, tzinfo=zone)
        saturday = datetime.datetime(2026, 1, 3, 1, 30, tzinfo=zone)
        saturday_late = datetime.datetime(2026, 1, 3, 2, 30, tzinfo=zone)
        utc = lambda value: value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        self.assertTrue(is_within_calling_window(utc(friday), "Asia/Shanghai", windows))
        self.assertTrue(is_within_calling_window(utc(saturday), "Asia/Shanghai", windows))
        self.assertTrue(is_within_calling_window(utc(saturday_late), "Asia/Shanghai", windows))
        self.assertFalse(is_within_calling_window(
            utc(saturday), "Asia/Shanghai", windows, ["2026-01-02", "2026-01-03"]
        ))

    def test_dst_nonexistent_and_ambiguous_windows_are_disabled(self):
        zone = ZoneInfo("America/New_York")
        nonexistent = {"days": [7], "start": "02:00", "end": "03:00"}
        ambiguous = {"days": [7], "start": "01:00", "end": "02:00"}
        self.assertIsNone(window_bounds(nonexistent, datetime.date(2026, 3, 8), zone))
        self.assertIsNone(window_bounds(ambiguous, datetime.date(2026, 11, 1), zone))

    def test_next_window_and_validation(self):
        now = datetime.datetime(2026, 1, 2, 3, 0)
        windows = [{"days": [5], "start": "12:00", "end": "13:00"}]
        next_at = next_calling_window(now, "Asia/Shanghai", windows)
        self.assertEqual(next_at, datetime.datetime(2026, 1, 2, 4, 0))
        for invalid in (
            [{"days": [1, 1], "start": "10:00", "end": "11:00"}],
            [{"days": [1], "start": "10:00", "end": "10:00"}],
            [{"days": [0], "start": "10:00", "end": "11:00"}],
        ):
            with self.assertRaises(Exception):
                validate_calling_windows(invalid)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.uow = SqlAlchemyUnitOfWork(self.sessions())
        self.codec = SecretCodec({"v1": b"k" * 32}, "v1")
        self.hmac = PhoneHmac({"v1": b"h" * 32}, "v1")
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
        self.uow.tasks.add(task_record())
        contact = contact_record()
        contact.phone_ciphertext = self.codec.encrypt("+15550000001")
        contact.variables_ciphertext = self.codec.encrypt("{}")
        self.uow.contacts.add(contact)
        self.uow.commit()

    def tearDown(self):
        self.uow.close()
        self.engine.dispose()

    def _scheduler(self, dispatcher, global_max=2):
        return DialScheduler(
            lambda: SqlAlchemyUnitOfWork(self.sessions()), dispatcher, self.codec, self.hmac,
            global_max_concurrent=global_max, clock=lambda: NOW,
        )

    def test_scan_creates_and_dispatches_one_reserved_call(self):
        async def scenario():
            dispatcher = FakeDispatcher()
            created = await self._scheduler(dispatcher).scan_once()
            self.assertEqual(len(created), 1)
            self.assertEqual(dispatcher.calls, created)
            call = self.uow.calls.get(created[0])
            self.assertEqual(call.status, "reserved")
            self.assertEqual(self.uow.contacts.get("contact_1").active_call_id, call.id)

        asyncio.run(scenario())

    def test_capacity_and_paused_task_leave_contact_pending(self):
        self.uow.tasks.transition_status("task_1", "paused", 1, NOW)
        self.uow.commit()

        async def scenario():
            dispatcher = FakeDispatcher()
            self.assertEqual(await self._scheduler(dispatcher).scan_once(), [])
            self.assertEqual(dispatcher.calls, [])

        asyncio.run(scenario())

    def test_due_retry_is_promoted_and_expired_unauthorized_reservation_recovered(self):
        contact = self.uow.session.get(TaskContactModel, "contact_1")
        contact.status = "waiting_retry"
        contact.retry_count = 1
        contact.next_call_at = NOW
        self.uow.commit()

        async def scenario():
            dispatcher = FakeDispatcher()
            created = await self._scheduler(dispatcher).scan_once()
            self.assertEqual(len(created), 1)
            self.assertEqual(self.uow.contacts.get("contact_1").retry_count, 1)

        asyncio.run(scenario())

    def test_expired_reservation_is_recovered_without_count_or_retry_quota(self):
        self.uow.calls.reserve("contact_1", "call_1", "origin_1", NOW, NOW)
        self.uow.commit()
        scheduler = self._scheduler(FakeDispatcher())
        self.assertEqual(scheduler.recover_expired_reservations(), ["call_1"])
        contact = self.uow.contacts.get("contact_1")
        self.assertEqual(contact.status, "pending")
        self.assertEqual(contact.retry_count, 0)
        self.assertEqual(self.uow.results.call_history("contact_1")["call_count"], 0)


if __name__ == "__main__":
    unittest.main()
