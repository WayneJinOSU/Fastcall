import datetime
from dataclasses import dataclass
from typing import Protocol


@dataclass
class OutboundTaskRecord:
    id: str
    customer_task_id: str
    name: str
    robot_app_id: str | None
    robot_version_id: str | None
    config_hash: str | None
    status: str
    health_status: str
    timezone: str
    default_country_code: str
    scheduled_start_at: datetime.datetime | None
    scheduled_end_at: datetime.datetime | None
    calling_windows_json: list
    excluded_dates_json: list
    max_concurrent_calls: int
    calls_per_minute: int
    next_dial_at: datetime.datetime | None
    last_dispatched_at: datetime.datetime | None
    retry_policy_json: dict
    callback_url: str | None
    callback_secret_ref: str | None
    state_version: int
    result_revision: int
    last_error_code: str | None
    created_at: datetime.datetime
    started_at: datetime.datetime | None
    completed_at: datetime.datetime | None
    created_by: str


@dataclass
class TaskContactRecord:
    id: str
    task_id: str
    customer_contact_id: str
    import_seq: int
    phone_ciphertext: bytes
    phone_last4: str
    phone_hmac: str
    hmac_key_version: str
    variables_ciphertext: bytes
    encryption_key_version: str
    status: str
    retry_count: int
    active_call_id: str | None
    next_call_at: datetime.datetime | None
    final_result_code: str | None
    last_error_code: str | None
    result_revision: int
    state_version: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
    finished_at: datetime.datetime | None


@dataclass
class OutboundCallRecord:
    id: str
    task_id: str
    contact_id: str
    call_no: int
    status: str
    result_code: str | None
    retryable: bool | None
    robot_version_id: str
    config_hash: str
    adapter_boot_id: str | None
    reserved_until: datetime.datetime | None
    origination_uuid: str
    freeswitch_uuid: str | None
    claim_generation: int
    session_state: str
    session_ready_at: datetime.datetime | None
    dial_phase: str
    dial_requested_at: datetime.datetime | None
    dial_acknowledged_at: datetime.datetime | None
    check_deadline_at: datetime.datetime | None
    answered_by_human: bool | None
    raw_hangup_cause: str | None
    hangup_source: str | None
    failure_code: str | None
    failure_message: str | None
    termination_reason: str | None
    reviewed_by: str | None
    reviewed_at: datetime.datetime | None
    review_evidence_ref: str | None
    review_note: str | None
    state_version: int
    created_at: datetime.datetime
    dialing_at: datetime.datetime | None
    ringing_at: datetime.datetime | None
    answered_at: datetime.datetime | None
    conversation_started_at: datetime.datetime | None
    ended_at: datetime.datetime | None
    duration_sec: int | None


@dataclass
class CallEventRecord:
    id: str
    source: str
    event_id: str
    event_type: str
    task_id: str
    contact_id: str
    call_id: str
    occurred_at: datetime.datetime
    received_at: datetime.datetime
    payload_json: dict
    payload_hash: str
    process_status: str
    process_owner: str | None
    process_lease_until: datetime.datetime | None
    process_attempts: int
    next_process_at: datetime.datetime | None
    processed_at: datetime.datetime | None
    last_error: str | None


@dataclass
class WebhookDeliveryRecord:
    id: str
    event_id: str
    aggregate_type: str
    aggregate_id: str
    aggregate_revision: int
    task_id: str
    contact_id: str | None
    call_id: str | None
    event_type: str
    endpoint_url: str
    payload_json: dict
    status: str
    delivery_owner: str | None
    delivery_lease_until: datetime.datetime | None
    attempt_count: int
    next_delivery_at: datetime.datetime | None
    last_http_status: int | None
    last_error: str | None
    delivered_at: datetime.datetime | None
    created_at: datetime.datetime


class OutboundTaskRepository(Protocol):
    def add(self, record: OutboundTaskRecord) -> None: ...
    def get(self, task_id: str) -> OutboundTaskRecord | None: ...
    def get_for_update(self, task_id: str) -> OutboundTaskRecord | None: ...
    def transition_status(self, task_id: str, target: str, expected_state_version: int, now): ...


class TaskContactRepository(Protocol):
    def add(self, record: TaskContactRecord) -> None: ...
    def get(self, contact_id: str) -> TaskContactRecord | None: ...
    def get_for_update(self, contact_id: str) -> TaskContactRecord | None: ...
    def find_by_phone_hmacs(self, task_id: str, phone_hmacs) -> TaskContactRecord | None: ...
    def list_due(self, task_id: str, now) -> list[TaskContactRecord]: ...
    def transition_status(self, contact_id: str, target: str, expected_state_version: int, now): ...


class OutboundCallRepository(Protocol):
    def get(self, call_id: str) -> OutboundCallRecord | None: ...
    def get_for_update(self, call_id: str) -> OutboundCallRecord | None: ...
    def count_active(self, task_id: str | None = None) -> int: ...
    def reserve(self, contact_id: str, call_id: str, origination_uuid: str, now, reserved_until): ...
    def release_unauthorized_reservation(self, call_id: str, expected_state_version: int, now): ...


class CallEventRepository(Protocol):
    def enqueue(self, record: CallEventRecord) -> bool: ...
    def has_event(self, call_id: str, event_type: str) -> bool: ...


class WebhookDeliveryRepository(Protocol):
    def enqueue(self, record: WebhookDeliveryRecord) -> bool: ...


class CallBlacklistRepository(Protocol):
    def is_blocked(self, hmac_versions, now) -> bool: ...


class SchedulerStateRepository(Protocol):
    def get_for_update(self): ...
    def initialize(self): ...


class OutboundUnitOfWork(Protocol):
    tasks: OutboundTaskRepository
    contacts: TaskContactRepository
    calls: OutboundCallRepository
    call_events: CallEventRepository
    webhook_deliveries: WebhookDeliveryRepository
    blacklist: CallBlacklistRepository
    scheduler_state: SchedulerStateRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
