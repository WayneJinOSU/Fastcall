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

    def test_only_fastcall_environment_names_are_accepted(self):
        from fastcall_adapter.settings import Settings as AdapterSettings

        values = {
            "APP_ENV": "test",
            "FASTCALL_DATABASE_URL": "mysql+pymysql://new/db",
            "FASTCALL_MANAGEMENT_TOKEN": "new-management",
            "FASTCALL_RESOLVE_TOKEN": "new-resolve",
        }
        with patch.dict("os.environ", values, clear=True):
            settings = Settings.from_env()
            adapter_settings = AdapterSettings.from_env()
        self.assertEqual(settings.database_url, "mysql+pymysql://new/db")
        self.assertEqual(settings.management_token, "new-management")
        self.assertEqual(settings.resolve_token, "new-resolve")
        self.assertEqual(adapter_settings.database_url, "mysql+pymysql://new/db")

        with patch.dict("os.environ", {
            "APP_ENV": "test",
            "ROBOT_CONFIG_DATABASE_URL": "mysql+pymysql://old/db",
            "ROBOT_CONFIG_MANAGEMENT_TOKEN": "old-management",
            "ROBOT_CONFIG_RESOLVE_TOKEN": "old-resolve",
        }, clear=True):
            settings = Settings.from_env()
            adapter_settings = AdapterSettings.from_env()
        self.assertEqual(settings.database_url, "")
        self.assertEqual(settings.management_token, "")
        self.assertEqual(settings.resolve_token, "")
        self.assertEqual(adapter_settings.database_url, "")


class FakeDialect:
    name = "mysql"


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, statement):
        return None


class FakeEngine:
    dialect = FakeDialect()

    def connect(self):
        return FakeConnection()


class SchemaStartupTests(unittest.TestCase):
    def test_missing_schema_is_rejected(self):
        database = Database(FakeEngine(), None)
        with patch("fastcall.infrastructure.database.inspect") as inspector:
            inspector.return_value.get_table_names.return_value = []
            with self.assertRaisesRegex(RuntimeError, "schema is missing"):
                database.verify()

    def test_missing_required_columns_are_rejected(self):
        database = Database(FakeEngine(), None)
        tables = [
            "robot_app", "robot_app_version", "robot_release",
            "robot_config_audit_log", "outbound_task", "task_contact",
            "outbound_call", "call_event", "call_blacklist", "webhook_delivery",
            "scheduler_state", "outbound_call_analysis",
        ]
        with patch("fastcall.infrastructure.database.inspect") as inspector:
            inspector.return_value.get_table_names.return_value = tables
            inspector.return_value.get_columns.return_value = []
            with self.assertRaisesRegex(RuntimeError, "missing columns"):
                database.verify()

    def test_complete_mysql_schema_is_accepted(self):
        database = Database(FakeEngine(), None)
        tables = [
            "robot_app", "robot_app_version", "robot_release",
            "robot_config_audit_log", "outbound_task", "task_contact",
            "outbound_call", "call_event", "call_blacklist", "webhook_delivery",
            "scheduler_state", "outbound_call_analysis",
        ]
        columns = {
            "origin", "mock_browser_uuid", "claim_generation", "dial_phase",
            "business_result",
        }
        with patch("fastcall.infrastructure.database.inspect") as inspector:
            inspector.return_value.get_table_names.return_value = tables
            inspector.return_value.get_columns.side_effect = lambda table: [
                {"name": name} for name in columns
            ]
            database.verify()


if __name__ == "__main__":
    unittest.main()
