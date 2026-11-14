from enum import Enum


class TelephonyResult(str, Enum):
    NO_ANSWER = "NO_ANSWER"
    USER_BUSY = "USER_BUSY"
    TEMPORARY_NETWORK_FAILURE = "TEMPORARY_NETWORK_FAILURE"
    CALL_REJECTED = "CALL_REJECTED"
    INVALID_NUMBER = "INVALID_NUMBER"
    UNALLOCATED_NUMBER = "UNALLOCATED_NUMBER"
    USER_HANGUP = "USER_HANGUP"
    VOICEMAIL = "VOICEMAIL"
    ORIGINATE_RESULT_UNKNOWN = "ORIGINATE_RESULT_UNKNOWN"
    CONFIRMED_NOT_DIALED = "CONFIRMED_NOT_DIALED"
    CALL_CHECK_TIMEOUT = "CALL_CHECK_TIMEOUT"
    AGENT_READY_TIMEOUT = "AGENT_READY_TIMEOUT"
    MEDIA_CONNECTION_TIMEOUT = "MEDIA_CONNECTION_TIMEOUT"
    MEDIA_BACKPRESSURE_OVERFLOW = "MEDIA_BACKPRESSURE_OVERFLOW"
    AI_PROVIDER_FAILED = "AI_PROVIDER_FAILED"


DEFAULT_RETRYABLE_RESULTS = frozenset({
    TelephonyResult.NO_ANSWER.value,
    TelephonyResult.USER_BUSY.value,
    TelephonyResult.TEMPORARY_NETWORK_FAILURE.value,
})

SUCCESS_RESULTS = frozenset({
    TelephonyResult.USER_HANGUP.value,
    TelephonyResult.VOICEMAIL.value,
})


def map_channel_hangup(cause, *, answered, ready, agent_failure=None):
    if agent_failure:
        return agent_failure
    if answered:
        if ready:
            return TelephonyResult.USER_HANGUP.value
        return TelephonyResult.AGENT_READY_TIMEOUT.value
    mapping = {
        "USER_BUSY": TelephonyResult.USER_BUSY.value,
        "CALL_REJECTED": TelephonyResult.CALL_REJECTED.value,
        "UNALLOCATED_NUMBER": TelephonyResult.UNALLOCATED_NUMBER.value,
        "INVALID_NUMBER": TelephonyResult.INVALID_NUMBER.value,
        "NO_ANSWER": TelephonyResult.NO_ANSWER.value,
        "NO_USER_RESPONSE": TelephonyResult.NO_ANSWER.value,
        "ALLOTTED_TIMEOUT": TelephonyResult.NO_ANSWER.value,
        "NORMAL_CLEARING": TelephonyResult.NO_ANSWER.value,
        "ORIGINATOR_CANCEL": TelephonyResult.NO_ANSWER.value,
        "NETWORK_OUT_OF_ORDER": TelephonyResult.TEMPORARY_NETWORK_FAILURE.value,
        "TEMPORARY_FAILURE": TelephonyResult.TEMPORARY_NETWORK_FAILURE.value,
        "SERVICE_UNAVAILABLE": TelephonyResult.TEMPORARY_NETWORK_FAILURE.value,
    }
    return mapping.get(cause, TelephonyResult.TEMPORARY_NETWORK_FAILURE.value)
