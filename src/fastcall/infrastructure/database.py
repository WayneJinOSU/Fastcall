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
REQUIRED_COLUMNS = {
    "outbound_task": {"origin"},
    "outbound_call": {"mock_browser_uuid", "claim_generation", "dial_phase"},
    "outbound_call_analysis": {"business_result"},
}


class Database(CoreDatabase):
    def verify(self) -> None:
        if self.engine.dialect.name != "mysql":
            raise RuntimeError("Fast-Call only supports MySQL")
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            inspector = inspect(connection)
            missing = REQUIRED_TABLES - set(inspector.get_table_names())
            if missing:
                raise RuntimeError(f"Fast-Call schema is missing: {sorted(missing)}")
            for table, required in REQUIRED_COLUMNS.items():
                missing_columns = required - {
                    column["name"] for column in inspector.get_columns(table)
                }
                if missing_columns:
                    raise RuntimeError(
                        f"Fast-Call schema is missing columns in {table}: {sorted(missing_columns)}"
                    )
