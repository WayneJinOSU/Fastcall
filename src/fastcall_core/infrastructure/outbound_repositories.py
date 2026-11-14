from dataclasses import fields

from sqlalchemy import func, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError

from fastcall_core.application.outbound_ports import (
    CallEventRecord,
    OutboundCallRecord,
    OutboundTaskRecord,
    TaskContactRecord,
    WebhookDeliveryRecord,
)
from fastcall_core.domain.calls import (
    ACTIVE_CALL_STATUSES,
    CallStatus,
    DialPhase,
    SessionState,
)
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.domain.outbound_tasks import (
    ContactStatus,
    TaskStatus,
    TERMINAL_CONTACT_STATUSES,
    validate_contact_transition,
    validate_task_transition,
)
from fastcall_core.infrastructure.outbound_orm import (
    CallBlacklistModel,
    CallEventModel,
    OutboundCallModel,
    OutboundTaskModel,
    SchedulerStateModel,
    TaskContactModel,
    WebhookDeliveryModel,
)


def _to_record(record_type, model):
    if model is None:
        return None
    return record_type(**{
        field.name: getattr(model, field.name) for field in fields(record_type)
    })


def _locked(session, model, resource_id):
    return session.execute(
        select(model)
        .where(model.id == resource_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    ).scalar_one_or_none()


def _require_version(record, expected_state_version):
    if record.state_version != expected_state_version:
        raise RobotConfigError(
            "STATE_VERSION_CONFLICT",
            "The resource changed; reload it before retrying",
            details={"current_state_version": record.state_version},
        )


def _compare_and_set(
    session, model, resource_id, expected_state_version, values, conditions=()
):
    result = session.execute(
        update(model)
        .where(
            model.id == resource_id,
            model.state_version == expected_state_version,
            *conditions,
        )
        .values(**values, state_version=expected_state_version + 1)
        .execution_options(synchronize_session=False)
    )
    session.expire_all()
    if result.rowcount != 1:
        raise RobotConfigError(
            "STATE_VERSION_CONFLICT",
            "The resource changed; reload it before retrying",
        )


def _validate_call_chain(session, task_id, contact_id, call_id, error_code):
    call = session.get(OutboundCallModel, call_id)
    if call is None or call.task_id != task_id or call.contact_id != contact_id:
        raise RobotConfigError(error_code, "Task, Contact and Call identifiers do not match")
    return call


class SqlAlchemyOutboundTaskRepository:
    def __init__(self, session):
        self.session = session

    def add(self, record: OutboundTaskRecord):
        self.session.add(OutboundTaskModel(**record.__dict__))
        self.session.flush()

    def get(self, task_id):
        return _to_record(OutboundTaskRecord, self.session.get(OutboundTaskModel, task_id))

    def get_by_customer_id(self, customer_task_id):
        model = self.session.execute(
            select(OutboundTaskModel).where(
                OutboundTaskModel.customer_task_id == customer_task_id
            )
        ).scalar_one_or_none()
        return _to_record(OutboundTaskRecord, model)

    def get_for_update(self, task_id):
        return _to_record(
            OutboundTaskRecord, _locked(self.session, OutboundTaskModel, task_id)
        )

    def pin_robot(self, task_id, robot_version_id, config_hash, expected_state_version):
        task = self.get_for_update(task_id)
        if task is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        _require_version(task, expected_state_version)
        if task.status not in (TaskStatus.DRAFT.value, TaskStatus.SCHEDULED.value):
            raise RobotConfigError("ROBOT_VERSION_IMMUTABLE", "Robot version is already pinned")
        _compare_and_set(
            self.session,
            OutboundTaskModel,
            task_id,
            expected_state_version,
            {"robot_version_id": robot_version_id, "config_hash": config_hash},
            (OutboundTaskModel.status == task.status,),
        )
        self.session.flush()
        return self.get(task_id)

    def transition_status(self, task_id, target, expected_state_version, now):
        task = self.get_for_update(task_id)
        if task is None:
            raise RobotConfigError("OUTBOUND_TASK_NOT_FOUND", "Outbound task not found")
        _require_version(task, expected_state_version)
        target = validate_task_transition(task.status, target)
        values = {"status": target.value}
        if target == TaskStatus.RUNNING and task.started_at is None:
            values["started_at"] = now
        if target in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            values["completed_at"] = now
        if target == TaskStatus.CANCELLED:
            values["result_revision"] = task.result_revision + 1
        _compare_and_set(
            self.session,
            OutboundTaskModel,
            task_id,
            expected_state_version,
            values,
            (OutboundTaskModel.status == task.status,),
        )
        self.session.flush()
        return self.get(task_id)


class SqlAlchemyTaskContactRepository:
    def __init__(self, session):
        self.session = session

    def add(self, record: TaskContactRecord):
        self.session.add(TaskContactModel(**record.__dict__))
        self.session.flush()

    def get(self, contact_id):
        return _to_record(TaskContactRecord, self.session.get(TaskContactModel, contact_id))

    def get_for_update(self, contact_id):
        return _to_record(
            TaskContactRecord, _locked(self.session, TaskContactModel, contact_id)
        )

    def get_by_customer_id(self, task_id, customer_contact_id):
        model = self.session.execute(
            select(TaskContactModel).where(
                TaskContactModel.task_id == task_id,
                TaskContactModel.customer_contact_id == customer_contact_id,
            )
        ).scalar_one_or_none()
        return _to_record(TaskContactRecord, model)

    def find_by_phone_hmacs(self, task_id, phone_hmacs):
        phone_hmacs = list(phone_hmacs)
        if not phone_hmacs:
            raise RobotConfigError(
                "HMAC_KEYS_UNAVAILABLE", "At least one accepted HMAC key version is required"
            )
        model = self.session.execute(
            select(TaskContactModel).where(
                TaskContactModel.task_id == task_id,
                TaskContactModel.phone_hmac.in_(phone_hmacs),
            )
        ).scalar_one_or_none()
        return _to_record(TaskContactRecord, model)

    def transition_status(self, contact_id, target, expected_state_version, now):
        contact = self.get_for_update(contact_id)
        if contact is None:
            raise RobotConfigError("TASK_CONTACT_NOT_FOUND", "Task contact not found")
        _require_version(contact, expected_state_version)
        target = validate_contact_transition(contact.status, target)
        if contact.active_call_id is not None and target != ContactStatus.CALLING:
            raise RobotConfigError(
                "CALL_FINALIZATION_REQUIRED",
                "A Contact with an active Call must be changed by the Call finalization transaction",
            )
        values = {"status": target.value, "updated_at": now}
        if target in TERMINAL_CONTACT_STATUSES or target == ContactStatus.WAITING_RETRY:
            values["active_call_id"] = None
        if target in TERMINAL_CONTACT_STATUSES and contact.finished_at is None:
            values["finished_at"] = now
        _compare_and_set(
            self.session,
            TaskContactModel,
            contact_id,
            expected_state_version,
            values,
            (TaskContactModel.status == contact.status,),
        )
        self.session.flush()
        return self.get(contact_id)


class SqlAlchemyOutboundCallRepository:
    def __init__(self, session):
        self.session = session
        self.contacts = SqlAlchemyTaskContactRepository(session)
        self.tasks = SqlAlchemyOutboundTaskRepository(session)

    def get(self, call_id):
        return _to_record(OutboundCallRecord, self.session.get(OutboundCallModel, call_id))

    def get_for_update(self, call_id):
        return _to_record(
            OutboundCallRecord, _locked(self.session, OutboundCallModel, call_id)
        )

    def count_active(self, task_id=None):
        statement = select(func.count(OutboundCallModel.id)).where(
            OutboundCallModel.status.in_(sorted(status.value for status in ACTIVE_CALL_STATUSES))
        )
        if task_id is not None:
            statement = statement.where(OutboundCallModel.task_id == task_id)
        return self.session.execute(statement).scalar_one()

    def release_unauthorized_reservation(self, call_id, expected_state_version, now):
        existing = self.get(call_id)
        if existing is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        task = self.tasks.get_for_update(existing.task_id)
        contact = self.contacts.get_for_update(existing.contact_id)
        call = self.get_for_update(call_id)
        if task is None or contact is None or call is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        _require_version(call, expected_state_version)
        if call.status != CallStatus.RESERVED.value or call.dial_requested_at is not None:
            raise RobotConfigError(
                "RESERVATION_NOT_RELEASABLE",
                "Only a reserved Call without dial authorization can be released",
            )
        if contact.active_call_id != call_id:
            raise RobotConfigError(
                "ACTIVE_CALL_MISMATCH", "Contact active_call_id does not match the Call"
            )
        if contact.status != ContactStatus.CALLING.value:
            raise RobotConfigError(
                "CONTACT_NOT_CALLING", "Only a calling Contact can return to pending"
            )
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            expected_state_version,
            {
                "status": CallStatus.CANCELLED.value,
                "failure_code": "RESERVATION_EXPIRED",
                "termination_reason": "RESERVATION_EXPIRED",
                "ended_at": now,
            },
            (
                OutboundCallModel.status == CallStatus.RESERVED.value,
                OutboundCallModel.dial_requested_at.is_(None),
            ),
        )
        _compare_and_set(
            self.session,
            TaskContactModel,
            contact.id,
            contact.state_version,
            {
                "status": ContactStatus.PENDING.value,
                "active_call_id": None,
                "next_call_at": now,
                "updated_at": now,
            },
            (
                TaskContactModel.status == ContactStatus.CALLING.value,
                TaskContactModel.active_call_id == call_id,
            ),
        )
        self.session.flush()


class SqlAlchemyCallEventRepository:
    def __init__(self, session):
        self.session = session

    def enqueue(self, record: CallEventRecord):
        _validate_call_chain(
            self.session, record.task_id, record.contact_id, record.call_id, "EVENT_CALL_MISMATCH"
        )
        try:
            with self.session.begin_nested():
                self.session.add(CallEventModel(**record.__dict__))
                self.session.flush()
            return True
        except IntegrityError:
            existing = self.session.execute(
                select(CallEventModel)
                .where(
                    CallEventModel.source == record.source,
                    CallEventModel.event_id == record.event_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if existing is None:
                raise
            if existing.payload_hash != record.payload_hash:
                raise RobotConfigError(
                    "EVENT_ID_CONFLICT", "Event ID already exists with different content"
                )
            return False

    def has_event(self, call_id, event_type):
        return self.session.execute(
            select(CallEventModel.id).where(
                CallEventModel.call_id == call_id,
                CallEventModel.event_type == event_type,
            ).limit(1).with_for_update()
        ).scalar_one_or_none() is not None


class SqlAlchemyWebhookDeliveryRepository:
    def __init__(self, session):
        self.session = session

    def enqueue(self, record: WebhookDeliveryRecord):
        if record.call_id is not None:
            if record.contact_id is None:
                raise RobotConfigError("WEBHOOK_CALL_MISMATCH", "call_id requires contact_id")
            _validate_call_chain(
                self.session,
                record.task_id,
                record.contact_id,
                record.call_id,
                "WEBHOOK_CALL_MISMATCH",
            )
        try:
            with self.session.begin_nested():
                self.session.add(WebhookDeliveryModel(**record.__dict__))
                self.session.flush()
            return True
        except IntegrityError:
            existing = self.session.execute(
                select(WebhookDeliveryModel)
                .where(WebhookDeliveryModel.event_id == record.event_id)
                .with_for_update()
            ).scalar_one_or_none()
            if existing is None:
                raise
            if existing.payload_json != record.payload_json:
                raise RobotConfigError(
                    "WEBHOOK_EVENT_CONFLICT",
                    "Webhook event ID already exists with different content",
                )
            return False


class SqlAlchemyCallBlacklistRepository:
    def __init__(self, session):
        self.session = session

    def is_blocked(self, hmac_versions, now):
        hmac_versions = list(hmac_versions)
        if not hmac_versions:
            raise RobotConfigError(
                "HMAC_KEYS_UNAVAILABLE", "At least one accepted HMAC key version is required"
            )
        statement = select(CallBlacklistModel.id).where(
            tuple_(CallBlacklistModel.phone_hmac, CallBlacklistModel.hmac_key_version).in_(
                hmac_versions
            ),
            CallBlacklistModel.effective_at <= now,
            or_(
                CallBlacklistModel.expires_at.is_(None),
                CallBlacklistModel.expires_at > now,
            ),
        ).limit(1)
        return self.session.execute(statement).scalar_one_or_none() is not None


class SqlAlchemySchedulerStateRepository:
    def __init__(self, session):
        self.session = session

    def get_for_update(self):
        return self.session.execute(
            select(SchedulerStateModel)
            .where(SchedulerStateModel.id == 1)
            .execution_options(populate_existing=True)
            .with_for_update()
        ).scalar_one_or_none()

    def initialize(self):
        state = self.get_for_update()
        if state is None:
            state = SchedulerStateModel(id=1, next_dial_at=None)
            self.session.add(state)
            self.session.flush()
        return state
