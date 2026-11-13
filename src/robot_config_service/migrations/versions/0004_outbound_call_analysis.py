"""Separate Fast-Call analysis from the legacy call_analysis table.

Revision ID: 0004
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _fastcall_analysis_exists():
    inspector = sa.inspect(op.get_bind())
    return "business_result" in inspector.get_columns("outbound_call_analysis")


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "outbound_call_analysis" in tables:
        return
    if "call_analysis" not in tables:
        return
    columns = {column["name"] for column in inspector.get_columns("call_analysis")}
    if "business_result" in columns:
        op.rename_table("call_analysis", "outbound_call_analysis")


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "outbound_call_analysis" in tables and _fastcall_analysis_exists():
        if "call_analysis" not in tables:
            op.rename_table("outbound_call_analysis", "call_analysis")
