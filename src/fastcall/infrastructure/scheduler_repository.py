from sqlalchemy import func, or_, select, update

from fastcall_core.domain.calling_windows import is_within_calling_window
from fastcall_core.domain.calls import CallStatus, DialPhase, SessionState
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.domain.outbound_tasks import ContactStatus, TaskStatus
from fastcall_core.infrastructure.orm import RobotAppModel
from fastcall_core.infrastructure.outbound_orm import (
    OutboundCallModel,
    OutboundTaskModel,
    TaskContactModel,
)
from fastcall_core.infrastructure.outbound_repositories import (
    SqlAlchemyCallBlacklistRepository,
    SqlAlchemyOutboundCallRepository,
    SqlAlchemyOutboundTaskRepository,
    SqlAlchemySchedulerStateRepository,
    SqlAlchemyTaskContactRepository,
    _compare_and_set,
    _to_record,
)
from fastcall_core.application.outbound_ports import (
    OutboundCallRecord,
    OutboundTaskRecord,
    TaskContactRecord,
)


class SqlAlchemySchedulerRepository:
    def __init__(self, session):
        self.session = session
        self.tasks = SqlAlchemyOutboundTaskRepository(session)
        self.contacts = SqlAlchemySchedulingContactRepository(session)
        self.calls = SqlAlchemySchedulingCallRepository(session)

    def promote_scheduled_tasks(self, now):
        models = self.session.execute(
            select(OutboundTaskModel).where(
                OutboundTaskModel.status == TaskStatus.SCHEDULED.value,
                or_(OutboundTaskModel.scheduled_start_at.is_(None), OutboundTaskModel.scheduled_start_at <= now),
            )
        ).scalars().all()
        promoted = []
        for model in models:
            self.tasks.transition_status(model.id, TaskStatus.RUNNING.value, model.state_version, now)
            promoted.append(model.id)
        return promoted

    def list_running_tasks(self):
        models = self.session.execute(
            select(OutboundTaskModel)
            .where(OutboundTaskModel.status == TaskStatus.RUNNING.value)
            .order_by(
                OutboundTaskModel.last_dispatched_at.is_not(None),
                OutboundTaskModel.last_dispatched_at,
                OutboundTaskModel.created_at,
                OutboundTaskModel.id,
            )
        ).scalars().all()
        return [_to_record(OutboundTaskRecord, model) for model in models]

    def list_expired_reservations(self, now):
        models = self.session.execute(
            select(OutboundCallModel).where(
                OutboundCallModel.status == CallStatus.RESERVED.value,
                OutboundCallModel.dial_requested_at.is_(None),
                OutboundCallModel.reserved_until <= now,
            )
        ).scalars().all()
        return [_to_record(OutboundCallRecord, model) for model in models]

    def promote_due_retries(self, task_id, now):
        models = self.session.execute(
            select(TaskContactModel).where(
                TaskContactModel.task_id == task_id,
                TaskContactModel.status == ContactStatus.WAITING_RETRY.value,
                TaskContactModel.next_call_at <= now,
            )
        ).scalars().all()
        promoted = []
        for model in models:
            contact = self.contacts.get_for_update(model.id)
            _compare_and_set(
                self.session,
                TaskContactModel,
                contact.id,
                contact.state_version,
                {"status": ContactStatus.PENDING.value, "updated_at": now},
                (TaskContactModel.status == ContactStatus.WAITING_RETRY.value,),
            )
            promoted.append(contact.id)
        self.session.flush()
        return promoted

    def reserve_dialable(
        self, contact_id, call_id, origination_uuid, now, reserved_until,
        hmac_versions, global_max_concurrent, global_calls_per_minute,
    ):
        state = SqlAlchemySchedulerStateRepository(self.session).initialize()
        existing = self.contacts.get(contact_id)
        task = self.tasks.get(existing.task_id) if existing else None
        if existing is None or task is None:
            return None
        if task.status != TaskStatus.RUNNING.value or task.health_status != "healthy":
            return None
        if task.scheduled_start_at and task.scheduled_start_at > now:
            return None
        if task.scheduled_end_at and task.scheduled_end_at <= now:
            return None
        if not is_within_calling_window(
            now, task.timezone, task.calling_windows_json, task.excluded_dates_json
        ):
            return None
        if state.next_dial_at and state.next_dial_at > now:
            return None
        if task.next_dial_at and task.next_dial_at > now:
            return None
        if self.calls.count_active() >= global_max_concurrent:
            return None
        if self.calls.count_active(task.id) >= task.max_concurrent_calls:
            return None
        app = self.session.get(RobotAppModel, task.robot_app_id)
        if app is None or not app.enabled:
            return None
        if SqlAlchemyCallBlacklistRepository(self.session).is_blocked(hmac_versions, now):
            return None
        return self.calls.reserve(contact_id, call_id, origination_uuid, now, reserved_until)


class SqlAlchemySchedulingContactRepository(SqlAlchemyTaskContactRepository):
    def list_due(self, task_id, now):
        models = self.session.execute(
            select(TaskContactModel)
            .where(
                TaskContactModel.task_id == task_id,
                TaskContactModel.status == ContactStatus.PENDING.value,
                TaskContactModel.active_call_id.is_(None),
                or_(
                    TaskContactModel.next_call_at.is_(None),
                    TaskContactModel.next_call_at <= now,
                ),
            )
            .order_by(TaskContactModel.next_call_at, TaskContactModel.import_seq, TaskContactModel.id)
        ).scalars().all()
        return [_to_record(TaskContactRecord, model) for model in models]


class SqlAlchemySchedulingCallRepository(SqlAlchemyOutboundCallRepository):
    def reserve(self, contact_id, call_id, origination_uuid, now, reserved_until):
        existing = self.contacts.get(contact_id)
        if existing is None:
            raise RobotConfigError("TASK_CONTACT_NOT_FOUND", "Task contact not found")
        task = self.tasks.get_for_update(existing.task_id)
        contact = self.contacts.get_for_update(contact_id)
        if task is None or contact is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        if task.status != TaskStatus.RUNNING.value:
            raise RobotConfigError("OUTBOUND_TASK_NOT_RUNNING", "Task is not running")
        if not task.robot_version_id or not task.config_hash:
            raise RobotConfigError("ROBOT_VERSION_NOT_PINNED", "Task Robot version is not pinned")
        if contact.status != ContactStatus.PENDING.value or contact.active_call_id is not None:
            raise RobotConfigError("CONTACT_NOT_DIALABLE", "Contact is not available for dialing")
        call_no = self.session.execute(
            select(func.max(OutboundCallModel.call_no)).where(
                OutboundCallModel.contact_id == contact_id
            )
        ).scalar() or 0
        record = OutboundCallRecord(
            id=call_id,
            task_id=task.id,
            contact_id=contact.id,
            call_no=call_no + 1,
            status=CallStatus.RESERVED.value,
            result_code=None,
            retryable=None,
            robot_version_id=task.robot_version_id,
            config_hash=task.config_hash,
            adapter_boot_id=None,
            reserved_until=reserved_until,
            origination_uuid=origination_uuid,
            freeswitch_uuid=None,
            claim_generation=0,
            session_state=SessionState.UNCLAIMED.value,
            session_ready_at=None,
            dial_phase=DialPhase.PREPARED.value,
            dial_requested_at=None,
            dial_acknowledged_at=None,
            check_deadline_at=None,
            answered_by_human=None,
            raw_hangup_cause=None,
            hangup_source=None,
            failure_code=None,
            failure_message=None,
            termination_reason=None,
            reviewed_by=None,
            reviewed_at=None,
            review_evidence_ref=None,
            review_note=None,
            state_version=1,
            created_at=now,
            dialing_at=None,
            ringing_at=None,
            answered_at=None,
            conversation_started_at=None,
            ended_at=None,
            duration_sec=None,
        )
        self.session.add(OutboundCallModel(**record.__dict__))
        result = self.session.execute(
            update(TaskContactModel)
            .where(
                TaskContactModel.id == contact_id,
                TaskContactModel.state_version == contact.state_version,
                TaskContactModel.status == ContactStatus.PENDING.value,
                TaskContactModel.active_call_id.is_(None),
            )
            .values(
                status=ContactStatus.CALLING.value,
                active_call_id=call_id,
                state_version=contact.state_version + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        self.session.expire_all()
        if result.rowcount != 1:
            raise RobotConfigError("CONTACT_RESERVATION_CONFLICT", "Contact reservation changed")
        self.session.flush()
        return record
