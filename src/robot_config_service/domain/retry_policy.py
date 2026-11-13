import datetime
from dataclasses import dataclass

from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.domain.telephony_results import DEFAULT_RETRYABLE_RESULTS


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    retry_intervals_sec: tuple[int, ...]
    retry_on: frozenset[str]

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise RobotConfigError("INVALID_RETRY_POLICY", "Retry policy must be an object")
        max_retries = value.get("max_retries")
        intervals = value.get("retry_intervals_sec")
        retry_on = value.get("retry_on")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise RobotConfigError(
                "INVALID_RETRY_POLICY", "max_retries must be a non-negative integer"
            )
        if not isinstance(intervals, list) or len(intervals) != max_retries:
            raise RobotConfigError(
                "INVALID_RETRY_POLICY",
                "retry_intervals_sec must contain exactly max_retries entries",
            )
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in intervals):
            raise RobotConfigError(
                "INVALID_RETRY_POLICY", "Retry intervals must be positive integers"
            )
        if not isinstance(retry_on, list) or not retry_on:
            if max_retries:
                raise RobotConfigError("INVALID_RETRY_POLICY", "retry_on must be a non-empty list")
            retry_on = []
        if any(not isinstance(item, str) or item not in DEFAULT_RETRYABLE_RESULTS for item in retry_on):
            raise RobotConfigError(
                "INVALID_RETRY_POLICY", "retry_on contains a non-retryable result code"
            )
        if len(set(retry_on)) != len(retry_on):
            raise RobotConfigError("INVALID_RETRY_POLICY", "retry_on cannot contain duplicates")
        return cls(max_retries, tuple(intervals), frozenset(retry_on))

    def as_dict(self):
        return {
            "max_retries": self.max_retries,
            "retry_intervals_sec": list(self.retry_intervals_sec),
            "retry_on": sorted(self.retry_on),
        }

    def next_retry_at(self, result_code, retry_count, now):
        if isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0:
            raise RobotConfigError("INVALID_RETRY_COUNT", "retry_count must be non-negative")
        if not isinstance(now, datetime.datetime):
            raise RobotConfigError("INVALID_RETRY_TIME", "now must be a datetime")
        if result_code not in self.retry_on or retry_count >= self.max_retries:
            return None
        return now + datetime.timedelta(seconds=self.retry_intervals_sec[retry_count])
