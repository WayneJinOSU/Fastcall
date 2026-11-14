"""Store business analysis separately from telephony results.

Revision ID: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _create(table_name):
    op.create_table(
        table_name,
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "call_id", sa.String(40), sa.ForeignKey("outbound_call.id"),
            nullable=False, unique=True,
        ),
        sa.Column("business_result", sa.String(255), nullable=True),
        sa.Column("analysis_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(6), nullable=False),
        sa.Column("updated_at", sa.DateTime(6), nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "outbound_call_analysis" in tables:
        return
    if "call_analysis" in tables:
        columns = {column["name"] for column in inspector.get_columns("call_analysis")}
        if "business_result" not in columns:
            _create("outbound_call_analysis")
            return
    _create("call_analysis")


def downgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "outbound_call_analysis" in tables:
        op.drop_table("outbound_call_analysis")
    elif "call_analysis" in tables:
        columns = {column["name"] for column in inspector.get_columns("call_analysis")}
        if "business_result" in columns:
            op.drop_table("call_analysis")
