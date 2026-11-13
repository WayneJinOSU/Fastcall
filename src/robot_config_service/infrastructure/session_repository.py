from robot_config_service.domain.calls import CallStatus, SessionState, validate_call_transition
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.domain.outbound_tasks import TaskStatus
from robot_config_service.infrastructure.outbound_orm import (
    OutboundCallModel,
    OutboundTaskModel,
    TaskContactModel,
)
from robot_config_service.infrastructure.outbound_repositories import (
    SqlAlchemyCallEventRepository,
    SqlAlchemyOutboundCallRepository,
    SqlAlchemyOutboundTaskRepository,
    SqlAlchemyTaskContactRepository,
    _compare_and_set,
    _to_record,
)


class SqlAlchemyAgentSessionRepository:
    def __init__(self, session):
        self.session = session
        self.calls = SqlAlchemyOutboundCallRepository(session)
        self.tasks = SqlAlchemyOutboundTaskRepository(session)
        self.contacts = SqlAlchemyTaskContactRepository(session)
        self.events = SqlAlchemyCallEventRepository(session)

    def _lock_aggregate(self, call_id, allow_terminal_task=False):
        existing = self.calls.get(call_id)
        if existing is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        task = self.tasks.get_for_update(existing.task_id)
        contact = self.contacts.get_for_update(existing.contact_id)
        call = self.calls.get_for_update(call_id)
        if task is None or contact is None or call is None:
            raise RobotConfigError("OUTBOUND_CALL_NOT_FOUND", "Outbound call not found")
        if contact.active_call_id != call_id:
            raise RobotConfigError("ACTIVE_CALL_MISMATCH", "Contact active_call_id does not match")
        if not allow_terminal_task and task.status in (
            TaskStatus.CANCELLED.value, TaskStatus.COMPLETED.value
        ):
            raise RobotConfigError("OUTBOUND_TASK_TERMINAL", "Task is already terminal")
        return task, contact, call

    def _validate_claimable(self, call):
        if call.status != CallStatus.ANSWERED.value:
            raise RobotConfigError("SESSION_NOT_CLAIMABLE", "Call is not available for Agent Session")
        if call.session_ready_at is not None or call.session_state == SessionState.FAILED.value:
            raise RobotConfigError("SESSION_NOT_CLAIMABLE", "Session is ready or failed")
        if self.events.has_event(call.id, "session.failed"):
            raise RobotConfigError("SESSION_FAILED", "A session.failed event has already been recorded")

    def claim(self, call_id, recover=False):
        task, contact, call = self._lock_aggregate(call_id)
        self._validate_claimable(call)
        if recover:
            if call.session_state not in (SessionState.CLAIMED.value, SessionState.ATTACHED.value):
                raise RobotConfigError("SESSION_NOT_RECOVERABLE", "Session cannot be recovered")
            generation = call.claim_generation + 1
        else:
            if call.session_state == SessionState.FAILED.value:
                raise RobotConfigError("SESSION_FAILED", "Session has failed")
            if call.claim_generation:
                return call
            generation = 1
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call.id,
            call.state_version,
            {"claim_generation": generation, "session_state": SessionState.CLAIMED.value},
            (
                OutboundCallModel.session_ready_at.is_(None),
                OutboundCallModel.session_state != SessionState.FAILED.value,
            ),
        )
        self.session.flush()
        return self.calls.get(call_id)

    def attach(self, call_id, generation, config_hash):
        task, contact, call = self._lock_aggregate(call_id)
        if call.config_hash != config_hash:
            raise RobotConfigError("CONFIG_HASH_MISMATCH", "Agent configuration hash does not match")
        if call.claim_generation != generation:
            raise RobotConfigError("STALE_CLAIM_GENERATION", "Claim generation is not current")
        if call.session_ready_at is not None:
            raise RobotConfigError("SESSION_ALREADY_READY", "Session is already ready")
        if call.session_state == SessionState.ATTACHED.value:
            return call
        if call.session_state != SessionState.CLAIMED.value:
            raise RobotConfigError("SESSION_NOT_CLAIMED", "Session has not been claimed")
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            call.state_version,
            {"session_state": SessionState.ATTACHED.value},
            (
                OutboundCallModel.claim_generation == generation,
                OutboundCallModel.session_state == SessionState.CLAIMED.value,
                OutboundCallModel.session_ready_at.is_(None),
            ),
        )
        self.session.flush()
        return self.calls.get(call_id)

    def ready(self, call_id, generation, config_hash, now):
        task, contact, call = self._lock_aggregate(call_id)
        if call.config_hash != config_hash:
            raise RobotConfigError("CONFIG_HASH_MISMATCH", "Agent configuration hash does not match")
        if call.claim_generation != generation:
            raise RobotConfigError("STALE_CLAIM_GENERATION", "Claim generation is not current")
        if call.session_ready_at is not None:
            return call
        if call.session_state != SessionState.ATTACHED.value:
            raise RobotConfigError("SESSION_NOT_ATTACHED", "Session is not attached")
        values = {"session_ready_at": now, "conversation_started_at": now}
        if call.status == CallStatus.ANSWERED.value:
            values["status"] = validate_call_transition(call.status, CallStatus.TALKING).value
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            call.state_version,
            values,
            (
                OutboundCallModel.claim_generation == generation,
                OutboundCallModel.session_state == SessionState.ATTACHED.value,
                OutboundCallModel.session_ready_at.is_(None),
            ),
        )
        self.session.flush()
        return self.calls.get(call_id)
