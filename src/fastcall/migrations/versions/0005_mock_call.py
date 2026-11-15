"""Add Mock Call source and browser channel.

Revision ID: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    task_columns = {column["name"] for column in inspector.get_columns("outbound_task")}
    if "origin" not in task_columns:
        op.add_column(
            "outbound_task",
            sa.Column("origin", sa.String(32), nullable=False, server_default="outbound"),
        )
    call_columns = {column["name"] for column in inspector.get_columns("outbound_call")}
    if "mock_browser_uuid" not in call_columns:
        op.add_column("outbound_call", sa.Column("mock_browser_uuid", sa.String(40), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "mock_browser_uuid" in {column["name"] for column in inspector.get_columns("outbound_call")}:
        op.drop_column("outbound_call", "mock_browser_uuid")
    if "origin" in {column["name"] for column in inspector.get_columns("outbound_task")}:
        op.drop_column("outbound_task", "origin")
