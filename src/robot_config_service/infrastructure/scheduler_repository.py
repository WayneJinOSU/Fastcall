from sqlalchemy import or_, select

from robot_config_service.domain.calling_windows import is_within_calling_window
from robot_config_service.domain.calls import CallStatus
from robot_config_service.domain.outbound_tasks import ContactStatus, TaskStatus
from robot_config_service.infrastructure.orm import RobotAppModel
from robot_config_service.infrastructure.outbound_orm import (
    OutboundCallModel,
    OutboundTaskModel,
    TaskContactModel,
)
from robot_config_service.infrastructure.outbound_repositories import (
    SqlAlchemyCallBlacklistRepository,
    SqlAlchemyOutboundCallRepository,
    SqlAlchemyOutboundTaskRepository,
    SqlAlchemySchedulerStateRepository,
    SqlAlchemyTaskContactRepository,
    _compare_and_set,
    _to_record,
)
from robot_config_service.application.outbound_ports import OutboundCallRecord, OutboundTaskRecord


class SqlAlchemySchedulerRepository:
    def __init__(self, session):
        self.session = session
        self.tasks = SqlAlchemyOutboundTaskRepository(session)
        self.contacts = SqlAlchemyTaskContactRepository(session)
        self.calls = SqlAlchemyOutboundCallRepository(session)

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
