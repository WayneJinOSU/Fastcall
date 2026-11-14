import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from fastcall.infrastructure.database import Database
from fastcall.settings import Settings


class StartupTests(unittest.TestCase):
    def test_invalid_settings_reject_startup(self):
        cases = (
            Settings("local", "mysql+pymysql://u:p@host/db", "a", "b"),
            Settings("test", "sqlite://", "a", "b"),
            Settings("test", "mysql+pymysql://u:p@host/db", "same", "same"),
            Settings("test", "mysql+pymysql://u:p@host/db", "", "b"),
        )
        for settings in cases:
            with self.subTest(settings=settings):
                with self.assertRaises(RuntimeError):
                    settings.validate()

    def test_non_mysql_is_rejected(self):
        engine = create_engine("sqlite://")
        database = Database(engine, sessionmaker(engine))
        with self.assertRaisesRegex(RuntimeError, "only supports MySQL"):
            database.verify()


class FakeDialect:
    name = "mysql"


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeConnection:
    def __init__(self, revision="0004"):
        self.revision = revision

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, statement):
        return FakeResult(self.revision)


class FakeEngine:
    dialect = FakeDialect()

    def __init__(self, revision="0004"):
        self.revision = revision

    def connect(self):
        return FakeConnection(self.revision)


class SchemaStartupTests(unittest.TestCase):
    def test_missing_schema_is_rejected(self):
        database = Database(FakeEngine(), None)
        with patch("fastcall.infrastructure.database.inspect") as inspector:
            inspector.return_value.get_table_names.return_value = []
            with self.assertRaisesRegex(RuntimeError, "schema is missing"):
                database.verify()

    def test_wrong_alembic_revision_is_rejected(self):
        database = Database(FakeEngine(revision="old"), None)
        tables = [
            "robot_app", "robot_app_version", "robot_release",
            "robot_config_audit_log", "outbound_task", "task_contact",
            "outbound_call", "call_event", "call_blacklist", "webhook_delivery",
            "scheduler_state", "outbound_call_analysis", "robot_config_alembic_version",
        ]
        with patch("fastcall.infrastructure.database.inspect") as inspector:
            inspector.return_value.get_table_names.return_value = tables
            with self.assertRaisesRegex(RuntimeError, "revision"):
                database.verify()

    def test_complete_mysql_schema_is_accepted(self):
        database = Database(FakeEngine(), None)
        tables = [
            "robot_app", "robot_app_version", "robot_release",
            "robot_config_audit_log", "outbound_task", "task_contact",
            "outbound_call", "call_event", "call_blacklist", "webhook_delivery",
            "scheduler_state", "outbound_call_analysis", "robot_config_alembic_version",
        ]
        with patch("fastcall.infrastructure.database.inspect") as inspector:
            inspector.return_value.get_table_names.return_value = tables
            database.verify()


if __name__ == "__main__":
    unittest.main()
