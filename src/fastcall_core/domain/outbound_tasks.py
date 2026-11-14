from enum import Enum

from fastcall_core.domain.errors import RobotConfigError


class TaskStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ContactStatus(str, Enum):
    PENDING = "pending"
    CALLING = "calling"
    WAITING_RETRY = "waiting_retry"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


TASK_TRANSITIONS = {
    TaskStatus.DRAFT: frozenset({TaskStatus.SCHEDULED, TaskStatus.CANCELLED}),
    TaskStatus.SCHEDULED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset({
        TaskStatus.PAUSED,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
    }),
    TaskStatus.PAUSED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

CONTACT_TRANSITIONS = {
    ContactStatus.PENDING: frozenset({
        ContactStatus.CALLING,
        ContactStatus.FAILED,
        ContactStatus.CANCELLED,
    }),
    ContactStatus.CALLING: frozenset({
        ContactStatus.WAITING_RETRY,
        ContactStatus.SUCCEEDED,
        ContactStatus.FAILED,
        ContactStatus.UNKNOWN,
        ContactStatus.CANCELLED,
    }),
    ContactStatus.WAITING_RETRY: frozenset({
        ContactStatus.PENDING,
        ContactStatus.CANCELLED,
    }),
    ContactStatus.SUCCEEDED: frozenset(),
    ContactStatus.FAILED: frozenset(),
    ContactStatus.UNKNOWN: frozenset({ContactStatus.SUCCEEDED, ContactStatus.FAILED}),
    ContactStatus.CANCELLED: frozenset(),
}

TERMINAL_CONTACT_STATUSES = frozenset({
    ContactStatus.SUCCEEDED,
    ContactStatus.FAILED,
    ContactStatus.UNKNOWN,
    ContactStatus.CANCELLED,
})


def validate_task_transition(current, target) -> TaskStatus:
    current = TaskStatus(current)
    target = TaskStatus(target)
    if target not in TASK_TRANSITIONS[current]:
        raise RobotConfigError(
            "INVALID_TASK_STATE_TRANSITION",
            f"Task cannot transition from {current.value} to {target.value}",
        )
    return target


def validate_contact_transition(current, target) -> ContactStatus:
    current = ContactStatus(current)
    target = ContactStatus(target)
    if target not in CONTACT_TRANSITIONS[current]:
        raise RobotConfigError(
            "INVALID_CONTACT_STATE_TRANSITION",
            f"Contact cannot transition from {current.value} to {target.value}",
        )
    return target
