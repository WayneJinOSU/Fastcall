from fastcall_core.domain.calls import CallStatus, SessionState, validate_call_transition
from fastcall_core.domain.errors import RobotConfigError
from fastcall_core.domain.outbound_tasks import (
    ContactStatus,
    TaskStatus,
    TERMINAL_CONTACT_STATUSES,
    validate_contact_transition,
)
from fastcall_core.infrastructure.outbound_orm import (
    OutboundCallModel,
    OutboundTaskModel,
    TaskContactModel,
)
from fastcall_core.infrastructure.outbound_repositories import _compare_and_set
from fastcall_core.infrastructure.session_repository import SqlAlchemyAgentSessionRepository
from sqlalchemy import func, select


class SqlAlchemyCallResultRepository:
    def __init__(self, session):
        self.session = session
        self.aggregate = SqlAlchemyAgentSessionRepository(session)

    def finalize(
        self, call_id, target, result_code, contact_target, now, *, generation=None,
        failure_code=None, failure_message=None, termination_reason=None,
        answered_by_human=None, session_failed=False, retry_at=None,
        raw_hangup_cause=None, expected_state_version=None, reviewed_by=None,
        review_evidence_ref=None, review_note=None,
    ):
        task, contact, call = self.aggregate._lock_aggregate(
            call_id, allow_terminal_task=True
        )
        target = CallStatus(target)
        contact_target = ContactStatus(contact_target)
        if generation is not None and generation > call.claim_generation:
            raise RobotConfigError("STALE_CLAIM_GENERATION", "Claim generation is not known")
        if (
            generation is not None
            and call.claim_generation != generation
            and call.session_ready_at is not None
        ):
            raise RobotConfigError("STALE_CLAIM_GENERATION", "Claim generation is not current")
        if expected_state_version is not None and call.state_version != expected_state_version:
            raise RobotConfigError("STATE_VERSION_CONFLICT", "Call state version does not match")
        validate_call_transition(call.status, target)
        validate_contact_transition(contact.status, contact_target)
        if contact.active_call_id != call_id:
            raise RobotConfigError("ACTIVE_CALL_MISMATCH", "Contact active_call_id does not match")
        duration = None
        if call.answered_at is not None:
            duration = max(0, int((now - call.answered_at).total_seconds()))
        values = {
            "status": target.value,
            "result_code": result_code,
            "retryable": retry_at is not None,
            "failure_code": failure_code,
            "failure_message": failure_message,
            "termination_reason": termination_reason,
            "answered_by_human": answered_by_human,
            "raw_hangup_cause": raw_hangup_cause,
            "reviewed_by": reviewed_by,
            "reviewed_at": now if reviewed_by else None,
            "review_evidence_ref": review_evidence_ref,
            "review_note": review_note,
            "ended_at": now,
            "duration_sec": duration,
        }
        if session_failed:
            values["session_state"] = SessionState.FAILED.value
        _compare_and_set(
            self.session,
            OutboundCallModel,
            call_id,
            call.state_version,
            values,
            (
                OutboundCallModel.status == call.status,
                OutboundCallModel.session_state != SessionState.FAILED.value,
            ),
        )
        first_terminal = (
            contact_target in TERMINAL_CONTACT_STATUSES
            and contact.status not in {status.value for status in TERMINAL_CONTACT_STATUSES}
        )
        contact_values = {
            "status": contact_target.value,
            "active_call_id": call_id if contact_target == ContactStatus.UNKNOWN else None,
            "next_call_at": retry_at,
            "last_error_code": failure_code,
            "updated_at": now,
        }
        if retry_at is not None:
            contact_values["retry_count"] = contact.retry_count + 1
        if contact_target in TERMINAL_CONTACT_STATUSES:
            contact_values.update({
                "final_result_code": result_code,
                "result_revision": contact.result_revision + 1,
                "finished_at": contact.finished_at or now,
            })
        _compare_and_set(
            self.session,
            TaskContactModel,
            contact.id,
            contact.state_version,
            contact_values,
            (
                TaskContactModel.status == contact.status,
                TaskContactModel.active_call_id == call_id,
            ),
        )
        self.session.flush()
        return {
            "task": self.aggregate.tasks.get(task.id),
            "contact": self.aggregate.contacts.get(contact.id),
            "call": self.aggregate.calls.get(call_id),
            "contact_first_terminal": first_terminal,
        }

    def call_history(self, contact_id):
        count = self.session.execute(
            select(func.count(OutboundCallModel.id)).where(
                OutboundCallModel.contact_id == contact_id,
                OutboundCallModel.dial_requested_at.is_not(None),
            )
        ).scalar_one()
        last = self.session.execute(
            select(OutboundCallModel)
            .where(OutboundCallModel.contact_id == contact_id)
            .order_by(OutboundCallModel.call_no.desc())
            .limit(1)
        ).scalar_one_or_none()
        return {"call_count": count, "last_call_id": last.id if last else None}

    def count_unfinished_contacts(self, task_id):
        terminal = [status.value for status in TERMINAL_CONTACT_STATUSES]
        return self.session.execute(
            select(func.count(TaskContactModel.id)).where(
                TaskContactModel.task_id == task_id,
                TaskContactModel.status.not_in(terminal),
            )
        ).scalar_one()

    def contact_status_counts(self, task_id):
        rows = self.session.execute(
            select(TaskContactModel.status, func.count(TaskContactModel.id))
            .where(TaskContactModel.task_id == task_id)
            .group_by(TaskContactModel.status)
        ).all()
        return {status: count for status, count in rows}

    def complete_task(self, task, now):
        if task.status == TaskStatus.COMPLETED.value:
            return task
        if task.status != TaskStatus.RUNNING.value:
            raise RobotConfigError("OUTBOUND_TASK_NOT_RUNNING", "Only a running task can complete")
        _compare_and_set(
            self.session,
            OutboundTaskModel,
            task.id,
            task.state_version,
            {
                "status": TaskStatus.COMPLETED.value,
                "completed_at": now,
                "result_revision": task.result_revision + 1,
            },
            (OutboundTaskModel.status == TaskStatus.RUNNING.value,),
        )
        self.session.flush()
        return self.aggregate.tasks.get(task.id)
