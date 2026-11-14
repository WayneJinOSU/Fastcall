"""Create Fast-Call outbound service tables.

Revision ID: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _table_options():
    return {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade():
    op.create_table(
        "outbound_task",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("customer_task_id", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("robot_app_id", sa.String(40), sa.ForeignKey("robot_app.id"), nullable=True),
        sa.Column("robot_version_id", sa.String(40), sa.ForeignKey("robot_app_version.id"), nullable=True),
        sa.Column("config_hash", sa.String(71), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("health_status", sa.String(32), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("default_country_code", sa.String(8), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(6), nullable=True),
        sa.Column("scheduled_end_at", sa.DateTime(6), nullable=True),
        sa.Column("calling_windows_json", sa.JSON(), nullable=False),
        sa.Column("excluded_dates_json", sa.JSON(), nullable=False),
        sa.Column("max_concurrent_calls", sa.Integer(), nullable=False),
        sa.Column("calls_per_minute", sa.Integer(), nullable=False),
        sa.Column("next_dial_at", sa.DateTime(6), nullable=True),
        sa.Column("last_dispatched_at", sa.DateTime(6), nullable=True),
        sa.Column("retry_policy_json", sa.JSON(), nullable=False),
        sa.Column("callback_url", sa.String(2048), nullable=True),
        sa.Column("callback_secret_ref", sa.String(255), nullable=True),
        sa.Column("state_version", sa.BigInteger(), nullable=False),
        sa.Column("result_revision", sa.BigInteger(), nullable=False),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        sa.Column("started_at", sa.DateTime(6), nullable=True),
        sa.Column("completed_at", sa.DateTime(6), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        **_table_options(),
    )
    op.create_table(
        "task_contact",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("outbound_task.id"), nullable=False),
        sa.Column("customer_contact_id", sa.String(128), nullable=False),
        sa.Column("import_seq", sa.BigInteger(), nullable=False),
        sa.Column("phone_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("phone_last4", sa.String(4), nullable=False),
        sa.Column("phone_hmac", sa.String(64), nullable=False),
        sa.Column("hmac_key_version", sa.String(64), nullable=False),
        sa.Column("variables_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_key_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("active_call_id", sa.String(40), nullable=True),
        sa.Column("next_call_at", sa.DateTime(6), nullable=True),
        sa.Column("final_result_code", sa.String(64), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("result_revision", sa.BigInteger(), nullable=False),
        sa.Column("state_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        sa.Column("updated_at", sa.DateTime(6), nullable=False),
        sa.Column("finished_at", sa.DateTime(6), nullable=True),
        sa.UniqueConstraint("task_id", "customer_contact_id", name="uq_contact_customer_id"),
        sa.UniqueConstraint("task_id", "phone_hmac", name="uq_contact_phone_hmac"),
        sa.UniqueConstraint("task_id", "import_seq", name="uq_contact_import_seq"),
        **_table_options(),
    )
    op.create_index(
        "ix_contact_task_dial", "task_contact",
        ["task_id", "status", "next_call_at", "import_seq", "id"],
    )
    op.create_index("ix_contact_status_dial", "task_contact", ["status", "next_call_at"])
    op.create_index(
        "ix_contact_task_active_call", "task_contact", ["task_id", "active_call_id"]
    )
    op.create_table(
        "outbound_call",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("outbound_task.id"), nullable=False),
        sa.Column("contact_id", sa.String(40), sa.ForeignKey("task_contact.id"), nullable=False),
        sa.Column("call_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("result_code", sa.String(64), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("robot_version_id", sa.String(40), sa.ForeignKey("robot_app_version.id"), nullable=False),
        sa.Column("config_hash", sa.String(71), nullable=False),
        sa.Column("adapter_boot_id", sa.String(40), nullable=True),
        sa.Column("reserved_until", sa.DateTime(6), nullable=True),
        sa.Column("origination_uuid", sa.String(40), nullable=False),
        sa.Column("freeswitch_uuid", sa.String(40), nullable=True),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("session_state", sa.String(32), nullable=False),
        sa.Column("session_ready_at", sa.DateTime(6), nullable=True),
        sa.Column("dial_phase", sa.String(32), nullable=False),
        sa.Column("dial_requested_at", sa.DateTime(6), nullable=True),
        sa.Column("dial_acknowledged_at", sa.DateTime(6), nullable=True),
        sa.Column("check_deadline_at", sa.DateTime(6), nullable=True),
        sa.Column("answered_by_human", sa.Boolean(), nullable=True),
        sa.Column("raw_hangup_cause", sa.String(64), nullable=True),
        sa.Column("hangup_source", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_message", sa.String(1024), nullable=True),
        sa.Column("termination_reason", sa.String(64), nullable=True),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(6), nullable=True),
        sa.Column("review_evidence_ref", sa.String(255), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("state_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        sa.Column("dialing_at", sa.DateTime(6), nullable=True),
        sa.Column("ringing_at", sa.DateTime(6), nullable=True),
        sa.Column("answered_at", sa.DateTime(6), nullable=True),
        sa.Column("conversation_started_at", sa.DateTime(6), nullable=True),
        sa.Column("ended_at", sa.DateTime(6), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.UniqueConstraint("contact_id", "call_no", name="uq_call_contact_no"),
        sa.UniqueConstraint("origination_uuid", name="uq_call_origination_uuid"),
        sa.UniqueConstraint("freeswitch_uuid", name="uq_call_freeswitch_uuid"),
        **_table_options(),
    )
    op.create_index("ix_call_status", "outbound_call", ["status"])
    op.create_index("ix_call_task_status", "outbound_call", ["task_id", "status"])
    op.create_index("ix_call_contact_no", "outbound_call", ["contact_id", "call_no"])
    op.create_table(
        "call_event",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("outbound_task.id"), nullable=False),
        sa.Column("contact_id", sa.String(40), sa.ForeignKey("task_contact.id"), nullable=False),
        sa.Column("call_id", sa.String(40), sa.ForeignKey("outbound_call.id"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(6), nullable=False),
        sa.Column("received_at", sa.DateTime(6), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("process_status", sa.String(32), nullable=False),
        sa.Column("process_owner", sa.String(64), nullable=True),
        sa.Column("process_lease_until", sa.DateTime(6), nullable=True),
        sa.Column("process_attempts", sa.Integer(), nullable=False),
        sa.Column("next_process_at", sa.DateTime(6), nullable=True),
        sa.Column("processed_at", sa.DateTime(6), nullable=True),
        sa.Column("last_error", sa.String(1024), nullable=True),
        sa.UniqueConstraint("source", "event_id", name="uq_call_event_source_id"),
        **_table_options(),
    )
    op.create_index(
        "ix_call_event_process", "call_event", ["process_status", "next_process_at"]
    )
    op.create_index("ix_call_event_call", "call_event", ["call_id", "event_type"])
    op.create_index(
        "ix_call_event_lease", "call_event", ["process_owner", "process_lease_until"]
    )
    op.create_table(
        "call_blacklist",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("phone_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("encryption_key_version", sa.String(64), nullable=False),
        sa.Column("phone_hmac", sa.String(64), nullable=False),
        sa.Column("hmac_key_version", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(1024), nullable=True),
        sa.Column("effective_at", sa.DateTime(6), nullable=False),
        sa.Column("expires_at", sa.DateTime(6), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        **_table_options(),
    )
    op.create_index(
        "ix_blacklist_hmac", "call_blacklist", ["phone_hmac", "hmac_key_version"]
    )
    op.create_index("ix_blacklist_expiry", "call_blacklist", ["expires_at"])
    op.create_table(
        "webhook_delivery",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(40), nullable=False),
        sa.Column("aggregate_revision", sa.BigInteger(), nullable=False),
        sa.Column("task_id", sa.String(40), sa.ForeignKey("outbound_task.id"), nullable=False),
        sa.Column("contact_id", sa.String(40), sa.ForeignKey("task_contact.id"), nullable=True),
        sa.Column("call_id", sa.String(40), sa.ForeignKey("outbound_call.id"), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("endpoint_url", sa.String(2048), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("delivery_owner", sa.String(64), nullable=True),
        sa.Column("delivery_lease_until", sa.DateTime(6), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_delivery_at", sa.DateTime(6), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(1024), nullable=True),
        sa.Column("delivered_at", sa.DateTime(6), nullable=True),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_webhook_event_id"),
        sa.UniqueConstraint(
            "event_type", "aggregate_type", "aggregate_id", "aggregate_revision",
            name="uq_webhook_aggregate_revision",
        ),
        **_table_options(),
    )
    op.create_index(
        "ix_webhook_delivery_due", "webhook_delivery", ["status", "next_delivery_at"]
    )
    op.create_index(
        "ix_webhook_delivery_lease", "webhook_delivery",
        ["delivery_owner", "delivery_lease_until"],
    )
    op.create_index(
        "ix_webhook_delivery_task", "webhook_delivery", ["task_id", "created_at"]
    )
    op.create_table(
        "scheduler_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("next_dial_at", sa.DateTime(6), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_scheduler_state_singleton"),
        **_table_options(),
    )


def downgrade():
    op.drop_table("scheduler_state")
    op.drop_table("webhook_delivery")
    op.drop_table("call_blacklist")
    op.drop_table("call_event")
    op.drop_table("outbound_call")
    op.drop_table("task_contact")
    op.drop_table("outbound_task")
