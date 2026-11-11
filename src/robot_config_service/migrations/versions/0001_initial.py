"""Create immutable robot configuration tables.

Revision ID: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "robot_app",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("app_code", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("draft_json", sa.JSON(), nullable=False),
        sa.Column("draft_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        sa.Column("updated_at", sa.DateTime(6), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_table(
        "robot_app_version",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("app_id", sa.String(40), sa.ForeignKey("robot_app.id"), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("source_draft_revision", sa.BigInteger(), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(71), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("runtime_engine", sa.String(32), nullable=False),
        sa.Column("runtime_adapter", sa.String(64), nullable=False),
        sa.Column("runtime_catalog_version", sa.String(32), nullable=False),
        sa.Column("validation_report_json", sa.JSON(), nullable=False),
        sa.Column("validated_at", sa.DateTime(6), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        sa.UniqueConstraint("app_id", "version_no", name="uq_robot_version_no"),
        sa.UniqueConstraint("app_id", "config_hash", name="uq_robot_version_hash"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_robot_version_draft", "robot_app_version", ["app_id", "source_draft_revision"])
    op.create_index("ix_robot_version_config_hash", "robot_app_version", ["config_hash"])
    op.create_table(
        "robot_release",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("app_id", sa.String(40), sa.ForeignKey("robot_app.id"), nullable=False, unique=True),
        sa.Column("robot_app_version_id", sa.String(40), sa.ForeignKey("robot_app_version.id"), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(6), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_table(
        "robot_config_audit_log",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(40), nullable=False),
        sa.Column("app_version_id", sa.String(40), nullable=True),
        sa.Column("config_hash", sa.String(71), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    for table in ("robot_app_version", "robot_config_audit_log"):
        op.execute(f"""
            CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
            FOR EACH ROW SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Robot Config Service records are immutable'
        """)
        op.execute(f"""
            CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            FOR EACH ROW SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Robot Config Service records are immutable'
        """)


def downgrade():
    for table in ("robot_app_version", "robot_config_audit_log"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
    op.drop_table("robot_config_audit_log")
    op.drop_table("robot_release")
    op.drop_index("ix_robot_version_config_hash", table_name="robot_app_version")
    op.drop_index("ix_robot_version_draft", table_name="robot_app_version")
    op.drop_table("robot_app_version")
    op.drop_table("robot_app")
