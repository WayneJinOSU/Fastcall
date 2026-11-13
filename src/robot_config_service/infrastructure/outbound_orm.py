from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)

from robot_config_service.infrastructure.orm import Base


class OutboundTaskModel(Base):
    __tablename__ = "outbound_task"

    id = Column(String(40), primary_key=True)
    customer_task_id = Column(String(128), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    robot_app_id = Column(String(40), ForeignKey("robot_app.id"), nullable=True)
    robot_version_id = Column(String(40), ForeignKey("robot_app_version.id"), nullable=True)
    config_hash = Column(String(71), nullable=True)
    status = Column(String(32), nullable=False)
    health_status = Column(String(32), nullable=False)
    timezone = Column(String(64), nullable=False)
    default_country_code = Column(String(8), nullable=False)
    scheduled_start_at = Column(DateTime(6), nullable=True)
    scheduled_end_at = Column(DateTime(6), nullable=True)
    calling_windows_json = Column(JSON(), nullable=False)
    excluded_dates_json = Column(JSON(), nullable=False)
    max_concurrent_calls = Column(Integer(), nullable=False)
    calls_per_minute = Column(Integer(), nullable=False)
    next_dial_at = Column(DateTime(6), nullable=True)
    last_dispatched_at = Column(DateTime(6), nullable=True)
    retry_policy_json = Column(JSON(), nullable=False)
    callback_url = Column(String(2048), nullable=True)
    callback_secret_ref = Column(String(255), nullable=True)
    state_version = Column(BigInteger(), nullable=False)
    result_revision = Column(BigInteger(), nullable=False)
    last_error_code = Column(String(64), nullable=True)
    created_at = Column(DateTime(6), nullable=False)
    started_at = Column(DateTime(6), nullable=True)
    completed_at = Column(DateTime(6), nullable=True)
    created_by = Column(String(128), nullable=False)


class TaskContactModel(Base):
    __tablename__ = "task_contact"
    __table_args__ = (
        UniqueConstraint("task_id", "customer_contact_id", name="uq_contact_customer_id"),
        UniqueConstraint("task_id", "phone_hmac", name="uq_contact_phone_hmac"),
        UniqueConstraint("task_id", "import_seq", name="uq_contact_import_seq"),
        Index(
            "ix_contact_task_dial",
            "task_id", "status", "next_call_at", "import_seq", "id",
        ),
        Index("ix_contact_status_dial", "status", "next_call_at"),
        Index("ix_contact_task_active_call", "task_id", "active_call_id"),
    )

    id = Column(String(40), primary_key=True)
    task_id = Column(String(40), ForeignKey("outbound_task.id"), nullable=False)
    customer_contact_id = Column(String(128), nullable=False)
    import_seq = Column(BigInteger(), nullable=False)
    phone_ciphertext = Column(LargeBinary(), nullable=False)
    phone_last4 = Column(String(4), nullable=False)
    phone_hmac = Column(String(64), nullable=False)
    hmac_key_version = Column(String(64), nullable=False)
    variables_ciphertext = Column(LargeBinary(), nullable=False)
    encryption_key_version = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    retry_count = Column(Integer(), nullable=False)
    active_call_id = Column(String(40), nullable=True)
    next_call_at = Column(DateTime(6), nullable=True)
    final_result_code = Column(String(64), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    result_revision = Column(BigInteger(), nullable=False)
    state_version = Column(BigInteger(), nullable=False)
    created_at = Column(DateTime(6), nullable=False)
    updated_at = Column(DateTime(6), nullable=False)
    finished_at = Column(DateTime(6), nullable=True)


class OutboundCallModel(Base):
    __tablename__ = "outbound_call"
    __table_args__ = (
        UniqueConstraint("contact_id", "call_no", name="uq_call_contact_no"),
        UniqueConstraint("origination_uuid", name="uq_call_origination_uuid"),
        UniqueConstraint("freeswitch_uuid", name="uq_call_freeswitch_uuid"),
        Index("ix_call_status", "status"),
        Index("ix_call_task_status", "task_id", "status"),
        Index("ix_call_contact_no", "contact_id", "call_no"),
    )

    id = Column(String(40), primary_key=True)
    task_id = Column(String(40), ForeignKey("outbound_task.id"), nullable=False)
    contact_id = Column(String(40), ForeignKey("task_contact.id"), nullable=False)
    call_no = Column(Integer(), nullable=False)
    status = Column(String(32), nullable=False)
    result_code = Column(String(64), nullable=True)
    retryable = Column(Boolean(), nullable=True)
    robot_version_id = Column(String(40), ForeignKey("robot_app_version.id"), nullable=False)
    config_hash = Column(String(71), nullable=False)
    adapter_boot_id = Column(String(40), nullable=True)
    reserved_until = Column(DateTime(6), nullable=True)
    origination_uuid = Column(String(40), nullable=False)
    freeswitch_uuid = Column(String(40), nullable=True)
    claim_generation = Column(Integer(), nullable=False)
    session_state = Column(String(32), nullable=False)
    session_ready_at = Column(DateTime(6), nullable=True)
    dial_phase = Column(String(32), nullable=False)
    dial_requested_at = Column(DateTime(6), nullable=True)
    dial_acknowledged_at = Column(DateTime(6), nullable=True)
    check_deadline_at = Column(DateTime(6), nullable=True)
    answered_by_human = Column(Boolean(), nullable=True)
    raw_hangup_cause = Column(String(64), nullable=True)
    hangup_source = Column(String(64), nullable=True)
    failure_code = Column(String(64), nullable=True)
    failure_message = Column(String(1024), nullable=True)
    termination_reason = Column(String(64), nullable=True)
    reviewed_by = Column(String(128), nullable=True)
    reviewed_at = Column(DateTime(6), nullable=True)
    review_evidence_ref = Column(String(255), nullable=True)
    review_note = Column(Text(), nullable=True)
    state_version = Column(BigInteger(), nullable=False)
    created_at = Column(DateTime(6), nullable=False)
    dialing_at = Column(DateTime(6), nullable=True)
    ringing_at = Column(DateTime(6), nullable=True)
    answered_at = Column(DateTime(6), nullable=True)
    conversation_started_at = Column(DateTime(6), nullable=True)
    ended_at = Column(DateTime(6), nullable=True)
    duration_sec = Column(Integer(), nullable=True)


class CallEventModel(Base):
    __tablename__ = "call_event"
    __table_args__ = (
        UniqueConstraint("source", "event_id", name="uq_call_event_source_id"),
        Index("ix_call_event_process", "process_status", "next_process_at"),
        Index("ix_call_event_call", "call_id", "event_type"),
        Index("ix_call_event_lease", "process_owner", "process_lease_until"),
    )

    id = Column(String(40), primary_key=True)
    source = Column(String(32), nullable=False)
    event_id = Column(String(255), nullable=False)
    event_type = Column(String(64), nullable=False)
    task_id = Column(String(40), ForeignKey("outbound_task.id"), nullable=False)
    contact_id = Column(String(40), ForeignKey("task_contact.id"), nullable=False)
    call_id = Column(String(40), ForeignKey("outbound_call.id"), nullable=False)
    occurred_at = Column(DateTime(6), nullable=False)
    received_at = Column(DateTime(6), nullable=False)
    payload_json = Column(JSON(), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    process_status = Column(String(32), nullable=False)
    process_owner = Column(String(64), nullable=True)
    process_lease_until = Column(DateTime(6), nullable=True)
    process_attempts = Column(Integer(), nullable=False)
    next_process_at = Column(DateTime(6), nullable=True)
    processed_at = Column(DateTime(6), nullable=True)
    last_error = Column(String(1024), nullable=True)


class CallBlacklistModel(Base):
    __tablename__ = "call_blacklist"
    __table_args__ = (
        Index("ix_blacklist_hmac", "phone_hmac", "hmac_key_version"),
        Index("ix_blacklist_expiry", "expires_at"),
    )

    id = Column(String(40), primary_key=True)
    phone_ciphertext = Column(LargeBinary(), nullable=False)
    encryption_key_version = Column(String(64), nullable=False)
    phone_hmac = Column(String(64), nullable=False)
    hmac_key_version = Column(String(64), nullable=False)
    source = Column(String(64), nullable=False)
    reason = Column(String(1024), nullable=True)
    effective_at = Column(DateTime(6), nullable=False)
    expires_at = Column(DateTime(6), nullable=True)
    created_by = Column(String(128), nullable=False)
    created_at = Column(DateTime(6), nullable=False)


class WebhookDeliveryModel(Base):
    __tablename__ = "webhook_delivery"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_webhook_event_id"),
        UniqueConstraint(
            "event_type", "aggregate_type", "aggregate_id", "aggregate_revision",
            name="uq_webhook_aggregate_revision",
        ),
        Index("ix_webhook_delivery_due", "status", "next_delivery_at"),
        Index("ix_webhook_delivery_lease", "delivery_owner", "delivery_lease_until"),
        Index("ix_webhook_delivery_task", "task_id", "created_at"),
    )

    id = Column(String(40), primary_key=True)
    event_id = Column(String(255), nullable=False)
    aggregate_type = Column(String(32), nullable=False)
    aggregate_id = Column(String(40), nullable=False)
    aggregate_revision = Column(BigInteger(), nullable=False)
    task_id = Column(String(40), ForeignKey("outbound_task.id"), nullable=False)
    contact_id = Column(String(40), ForeignKey("task_contact.id"), nullable=True)
    call_id = Column(String(40), ForeignKey("outbound_call.id"), nullable=True)
    event_type = Column(String(64), nullable=False)
    endpoint_url = Column(String(2048), nullable=False)
    payload_json = Column(JSON(), nullable=False)
    status = Column(String(32), nullable=False)
    delivery_owner = Column(String(64), nullable=True)
    delivery_lease_until = Column(DateTime(6), nullable=True)
    attempt_count = Column(Integer(), nullable=False)
    next_delivery_at = Column(DateTime(6), nullable=True)
    last_http_status = Column(Integer(), nullable=True)
    last_error = Column(String(1024), nullable=True)
    delivered_at = Column(DateTime(6), nullable=True)
    created_at = Column(DateTime(6), nullable=False)


class CallAnalysisModel(Base):
    __tablename__ = "outbound_call_analysis"

    id = Column(String(40), primary_key=True)
    call_id = Column(
        String(40), ForeignKey("outbound_call.id"), nullable=False, unique=True
    )
    business_result = Column(String(255), nullable=True)
    analysis_json = Column(JSON(), nullable=False)
    created_at = Column(DateTime(6), nullable=False)
    updated_at = Column(DateTime(6), nullable=False)


class SchedulerStateModel(Base):
    __tablename__ = "scheduler_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_scheduler_state_singleton"),
    )

    id = Column(Integer(), primary_key=True, autoincrement=False)
    next_dial_at = Column(DateTime(6), nullable=True)
