import datetime

from sqlalchemy import select

from fastcall_core.domain.calling_windows import is_within_calling_window
from fastcall_core.domain.calls import CallStatus, DialPhase
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.domain.outbound_tasks import TaskStatus
from fastcall_core.infrastructure.orm import RobotAppModel
from fastcall_core.infrastructure.outbound_orm import OutboundCallModel, OutboundTaskModel
from fastcall_core.infrastructure.outbound_repositories import (
    SqlAlchemyCallBlacklistRepository,
    SqlAlchemySchedulerStateRepository,
    _compare_and_set,
)
from fastcall_core.infrastructure.session_repository import SqlAlchemyAgentSessionRepository


class SqlAlchemyDispatchRepository:
    def __init__(self, session):
        self.session = session
        self.aggregate = SqlAlchemyAgentSessionRepository(session)

    def authorize(
        self, call_id, expected_state_version, adapter_boot_id, now,
        hmac_versions, global_max_concurrent, global_calls_per_minute,
    ):
        scheduler_state = SqlAlchemySchedulerStateRepository(self.session).initialize()
        task, contact, call = self.aggregate._lock_aggregate(call_id)
        if call.state_version != expected_state_version:
            raise RobotConfigError("STATE_VERSION_CONFLICT", "Call changed before dispatch")
        if call.status != CallStatus.RESERVED.value or call.dial_requested_at is not None:
            raise RobotConfigError("DIAL_NOT_AUTHORIZED", "Call is not an unauthorized reservation")
        if contact.status != "calling" or contact.active_call_id != call_id:
            raise RobotConfigError("ACTIVE_CALL_MISMATCH", "Contact reservation is not active")
        if task.status != TaskStatus.RUNNING.value or task.health_status != "healthy":
            raise RobotConfigError("OUTBOUND_TASK_NOT_RUNNING", "Task is not healthy and running")
        if task.scheduled_start_at and task.scheduled_start_at > now:
            raise RobotConfigError("OUTSIDE_CALLING_WINDOW", "Task has not started")
        if task.scheduled_end_at and task.scheduled_end_at <= now:
            raise RobotConfigError("OUTSIDE_CALLING_WINDOW", "Task has ended")
        if not is_within_calling_window(
            now, task.timezone, task.calling_windows_json, task.excluded_dates_json
        ):
            raise RobotConfigError("OUTSIDE_CALLING_WINDOW", "Outside the task calling window")
        if SqlAlchemyCallBlacklistRepository(self.session).is_blocked(hmac_versions, now):
            raise RobotConfigError("CALL_BLACKLISTED", "Phone number is blacklisted")
        app = self.session.get(RobotAppModel, task.robot_app_id)
        if app is None or not app.enabled:
            raise RobotConfigError("ROBOT_DISABLED", "Robot is disabled")
        if self.aggregate.calls.count_active() > global_max_concurrent:
            raise RobotConfigError("GLOBAL_CAPACITY_EXCEEDED", "Global call capacity is exhausted")
        if self.aggregate.calls.count_active(task.id) > task.max_concurrent_calls:
            raise RobotConfigError("TASK_CAPACITY_EXCEEDED", "Task call capacity is exhausted")
        if scheduler_state.next_dial_at and scheduler_state.next_dial_at > now:
            raise RobotConfigError("GLOBAL_RATE_LIMITED", "Global dial rate limit is active")
        if task.next_dial_at and task.next_dial_at > now:
            raise RobotConfigError("TASK_RATE_LIMITED", "Task dial rate limit is active")
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            expected_state_version,
            {
                "status": CallStatus.DIALING.value,
                "adapter_boot_id": adapter_boot_id,
                "dial_requested_at": now,
                "dial_phase": DialPhase.DIAL_REQUESTED.value,
                "dialing_at": now,
            },
            (
                OutboundCallModel.status == CallStatus.RESERVED.value,
                OutboundCallModel.dial_requested_at.is_(None),
            ),
        )
        _compare_and_set(
            self.session,
            OutboundTaskModel,
            task.id,
            task.state_version,
            {
                "next_dial_at": now + datetime.timedelta(seconds=60 / task.calls_per_minute),
                "last_dispatched_at": now,
            },
            (OutboundTaskModel.status == TaskStatus.RUNNING.value,),
        )
        scheduler_state.next_dial_at = now + datetime.timedelta(
            seconds=60 / global_calls_per_minute
        )
        self.session.flush()
        return self.aggregate.calls.get(call_id)

    def acknowledge(self, call_id, freeswitch_uuid, now):
        task, contact, call = self.aggregate._lock_aggregate(call_id, allow_terminal_task=True)
        if call.dial_requested_at is None:
            raise RobotConfigError("DIAL_NOT_AUTHORIZED", "Call has no dial authorization")
        if call.freeswitch_uuid is not None and call.freeswitch_uuid != freeswitch_uuid:
            raise RobotConfigError("CHANNEL_UUID_CONFLICT", "Call already has a different channel UUID")
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            call.state_version,
            {
                "freeswitch_uuid": freeswitch_uuid,
                "dial_phase": DialPhase.ACKNOWLEDGED.value,
                "dial_acknowledged_at": now,
            },
            (OutboundCallModel.dial_requested_at.is_not(None),),
        )
        self.session.flush()
        return self.aggregate.calls.get(call_id)

    def list_recoverable_sessions(self):
        models = self.session.execute(
            select(OutboundCallModel).where(
                OutboundCallModel.status == CallStatus.ANSWERED.value,
                OutboundCallModel.session_ready_at.is_(None),
                OutboundCallModel.session_state.in_(["claimed", "attached"]),
            )
        ).scalars().all()
        return [self.aggregate.calls.get(model.id) for model in models]

    def list_checking(self):
        models = self.session.execute(
            select(OutboundCallModel).where(OutboundCallModel.status == CallStatus.CHECKING.value)
        ).scalars().all()
        return [self.aggregate.calls.get(model.id) for model in models]

    def list_expired_checks(self, now):
        models = self.session.execute(
            select(OutboundCallModel).where(
                OutboundCallModel.status == CallStatus.CHECKING.value,
                OutboundCallModel.check_deadline_at <= now,
            )
        ).scalars().all()
        return [self.aggregate.calls.get(model.id) for model in models]

    def list_unacknowledged(self):
        models = self.session.execute(
            select(OutboundCallModel).where(
                OutboundCallModel.dial_requested_at.is_not(None),
                OutboundCallModel.dial_phase.in_([
                    DialPhase.DIAL_REQUESTED.value, DialPhase.UNKNOWN.value
                ]),
                OutboundCallModel.status.in_([
                    CallStatus.DIALING.value, CallStatus.CHECKING.value
                ]),
            )
        ).scalars().all()
        return [self.aggregate.calls.get(model.id) for model in models]

    def mark_checking(self, call_id, now, check_timeout_sec=900):
        task, contact, call = self.aggregate._lock_aggregate(call_id, allow_terminal_task=True)
        if call.status == CallStatus.CHECKING.value:
            return call
        if call.status not in (CallStatus.RESERVED.value, CallStatus.DIALING.value):
            raise RobotConfigError("CALL_NOT_CHECKABLE", "Only reserved or dialing Calls enter checking")
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            call.state_version,
            {
                "status": CallStatus.CHECKING.value,
                "dial_phase": DialPhase.UNKNOWN.value,
                "result_code": "ORIGINATE_RESULT_UNKNOWN",
                "check_deadline_at": now + datetime.timedelta(seconds=check_timeout_sec),
            },
            (OutboundCallModel.status == call.status,),
        )
        self.session.flush()
        return self.aggregate.calls.get(call_id)
