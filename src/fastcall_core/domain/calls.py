from enum import Enum

from fastcall_core.domain.errors import RobotConfigError


class CallStatus(str, Enum):
    RESERVED = "reserved"
    DIALING = "dialing"
    RINGING = "ringing"
    ANSWERED = "answered"
    TALKING = "talking"
    CHECKING = "checking"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class DialPhase(str, Enum):
    PREPARED = "prepared"
    DIAL_REQUESTED = "dial_requested"
    ACKNOWLEDGED = "acknowledged"
    UNKNOWN = "unknown"


class SessionState(str, Enum):
    UNCLAIMED = "unclaimed"
    CLAIMED = "claimed"
    ATTACHED = "attached"
    FAILED = "failed"


CALL_TRANSITIONS = {
    CallStatus.RESERVED: frozenset({
        CallStatus.DIALING,
        CallStatus.FAILED,
        CallStatus.CHECKING,
        CallStatus.CANCELLED,
    }),
    CallStatus.DIALING: frozenset({
        CallStatus.RINGING,
        CallStatus.ANSWERED,
        CallStatus.CHECKING,
        CallStatus.FAILED,
        CallStatus.CANCELLED,
    }),
    CallStatus.RINGING: frozenset({
        CallStatus.ANSWERED,
        CallStatus.CHECKING,
        CallStatus.FAILED,
        CallStatus.CANCELLED,
    }),
    CallStatus.ANSWERED: frozenset({
        CallStatus.TALKING,
        CallStatus.CHECKING,
        CallStatus.FAILED,
        CallStatus.CANCELLED,
    }),
    CallStatus.TALKING: frozenset({
        CallStatus.SUCCEEDED,
        CallStatus.FAILED,
        CallStatus.CANCELLED,
    }),
    CallStatus.CHECKING: frozenset({
        CallStatus.ANSWERED,
        CallStatus.SUCCEEDED,
        CallStatus.FAILED,
        CallStatus.UNKNOWN,
    }),
    CallStatus.SUCCEEDED: frozenset(),
    CallStatus.FAILED: frozenset(),
    CallStatus.CANCELLED: frozenset(),
    CallStatus.UNKNOWN: frozenset({CallStatus.SUCCEEDED, CallStatus.FAILED}),
}

ACTIVE_CALL_STATUSES = frozenset({
    CallStatus.RESERVED,
    CallStatus.DIALING,
    CallStatus.RINGING,
    CallStatus.ANSWERED,
    CallStatus.TALKING,
    CallStatus.CHECKING,
    CallStatus.UNKNOWN,
})

TERMINAL_CALL_STATUSES = frozenset({
    CallStatus.SUCCEEDED,
    CallStatus.FAILED,
    CallStatus.CANCELLED,
    CallStatus.UNKNOWN,
})


def validate_call_transition(current, target) -> CallStatus:
    current = CallStatus(current)
    target = CallStatus(target)
    if target not in CALL_TRANSITIONS[current]:
        raise RobotConfigError(
            "INVALID_CALL_STATE_TRANSITION",
            f"Call cannot transition from {current.value} to {target.value}",
        )
    return target
