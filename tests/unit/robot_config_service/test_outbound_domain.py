import datetime
import unittest

from robot_config_service.domain.calls import ACTIVE_CALL_STATUSES, CallStatus, validate_call_transition
from robot_config_service.domain.errors import RobotConfigError
from robot_config_service.domain.outbound_tasks import (
    ContactStatus,
    TaskStatus,
    validate_contact_transition,
    validate_task_transition,
)
from robot_config_service.domain.retry_policy import RetryPolicy


class OutboundStateTests(unittest.TestCase):
    def test_task_lifecycle(self):
        self.assertEqual(
            validate_task_transition(TaskStatus.DRAFT, TaskStatus.SCHEDULED),
            TaskStatus.SCHEDULED,
        )
        self.assertEqual(
            validate_task_transition(TaskStatus.SCHEDULED, TaskStatus.RUNNING),
            TaskStatus.RUNNING,
        )
        self.assertEqual(
            validate_task_transition(TaskStatus.RUNNING, TaskStatus.PAUSED),
            TaskStatus.PAUSED,
        )
        self.assertEqual(
            validate_task_transition(TaskStatus.PAUSED, TaskStatus.RUNNING),
            TaskStatus.RUNNING,
        )

    def test_invalid_and_terminal_transitions_are_rejected(self):
        cases = (
            (validate_task_transition, TaskStatus.DRAFT, TaskStatus.COMPLETED),
            (validate_task_transition, TaskStatus.CANCELLED, TaskStatus.RUNNING),
            (validate_contact_transition, ContactStatus.PENDING, ContactStatus.SUCCEEDED),
            (validate_contact_transition, ContactStatus.UNKNOWN, ContactStatus.CALLING),
            (validate_call_transition, CallStatus.RESERVED, CallStatus.SUCCEEDED),
            (validate_call_transition, CallStatus.UNKNOWN, CallStatus.DIALING),
        )
        for validator, current, target in cases:
            with self.subTest(current=current, target=target):
                with self.assertRaises(RobotConfigError):
                    validator(current, target)

    def test_checking_and_unknown_retain_capacity(self):
        self.assertIn(CallStatus.CHECKING, ACTIVE_CALL_STATUSES)
        self.assertIn(CallStatus.UNKNOWN, ACTIVE_CALL_STATUSES)


class RetryPolicyTests(unittest.TestCase):
    def test_valid_policy_schedules_next_retry_without_consuming_quota(self):
        policy = RetryPolicy.from_dict({
            "max_retries": 2,
            "retry_intervals_sec": [60, 120],
            "retry_on": ["NO_ANSWER"],
        })
        now = datetime.datetime(2026, 1, 1, 12, 0, 0)
        self.assertEqual(
            policy.next_retry_at("NO_ANSWER", 0, now),
            now + datetime.timedelta(seconds=60),
        )
        self.assertEqual(
            policy.next_retry_at("NO_ANSWER", 1, now),
            now + datetime.timedelta(seconds=120),
        )
        self.assertIsNone(policy.next_retry_at("NO_ANSWER", 2, now))
        self.assertIsNone(policy.next_retry_at("USER_BUSY", 0, now))

    def test_zero_retries_requires_no_intervals(self):
        policy = RetryPolicy.from_dict({
            "max_retries": 0,
            "retry_intervals_sec": [],
            "retry_on": [],
        })
        self.assertEqual(policy.max_retries, 0)
        self.assertIsNone(policy.next_retry_at(
            "NO_ANSWER", 0, datetime.datetime(2026, 1, 1)
        ))

    def test_invalid_retry_count_is_rejected_before_comparison(self):
        policy = RetryPolicy.from_dict({
            "max_retries": 0, "retry_intervals_sec": [], "retry_on": []
        })
        for retry_count in (True, "0", -1):
            with self.subTest(retry_count=retry_count):
                with self.assertRaises(RobotConfigError) as error:
                    policy.next_retry_at("NO_ANSWER", retry_count, datetime.datetime(2026, 1, 1))
                self.assertEqual(error.exception.code, "INVALID_RETRY_COUNT")

    def test_invalid_policies_are_rejected(self):
        cases = (
            {"max_retries": 1, "retry_intervals_sec": [], "retry_on": ["NO_ANSWER"]},
            {"max_retries": 1, "retry_intervals_sec": [0], "retry_on": ["NO_ANSWER"]},
            {"max_retries": True, "retry_intervals_sec": [60], "retry_on": ["NO_ANSWER"]},
            {"max_retries": 1, "retry_intervals_sec": [60], "retry_on": ["UNKNOWN_CODE"]},
            {"max_retries": 1, "retry_intervals_sec": [60], "retry_on": ["USER_HANGUP"]},
            {"max_retries": 2, "retry_intervals_sec": [60, 60], "retry_on": ["NO_ANSWER", "NO_ANSWER"]},
            {"max_retries": 1, "retry_intervals_sec": [60], "retry_on": []},
        )
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(RobotConfigError):
                    RetryPolicy.from_dict(value)


if __name__ == "__main__":
    unittest.main()
