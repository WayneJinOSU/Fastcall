from sqlalchemy import inspect, text

from fastcall_core.infrastructure.database import Database as CoreDatabase


REQUIRED_TABLES = {
    "robot_app",
    "robot_app_version",
    "robot_release",
    "robot_config_audit_log",
    "outbound_task",
    "task_contact",
    "outbound_call",
    "call_event",
    "call_blacklist",
    "webhook_delivery",
    "scheduler_state",
    "outbound_call_analysis",
}
ALEMBIC_TABLE = "robot_config_alembic_version"
EXPECTED_REVISION = "0004"


class Database(CoreDatabase):
    def verify(self) -> None:
        if self.engine.dialect.name != "mysql":
            raise RuntimeError("Fast-Call only supports MySQL")
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            table_names = set(inspect(connection).get_table_names())
            missing = (REQUIRED_TABLES | {ALEMBIC_TABLE}) - table_names
            if missing:
                raise RuntimeError(f"Fast-Call schema is missing: {sorted(missing)}")
            revision = connection.execute(
                text(f"SELECT version_num FROM {ALEMBIC_TABLE}")
            ).scalar()
            if revision != EXPECTED_REVISION:
                raise RuntimeError(
                    f"Fast-Call schema revision must be {EXPECTED_REVISION}"
                )
