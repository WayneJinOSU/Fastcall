import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from collection_agents.infra.paths import DATA_DIR
from collection_agents.metrics.collector import CallMetricsCollector
from collection_agents.storage.db import (
    _PROCESS_RECORD_LEGACY_COLUMNS,
    _SQLITE_DDL,
    _copy_legacy_process_records,
    _prepare_sqlite_process_record,
)


class TechnicalMetricsTests(unittest.TestCase):
    def test_record_contains_only_technical_fields_and_no_secrets(self):
        collector = CallMetricsCollector(
            call_uuid="call-1",
            robot_code="robot-1",
            robot_version_id=None,
            bot_mode="standard",
            task_start_time=datetime.datetime.now(),
            asr_info={"provider": "deepgram", "model": "nova-2", "api_key": "secret"},
            llm_info={
                "provider": "deepseek",
                "model": "deepseek-flash",
                "api_key": "secret",
                "prompt": "customer data",
            },
            tts_info={"provider": "cartesia", "model": "sonic-2", "api_key": "secret"},
        )

        record = collector.to_dict()
        self.assertEqual(record["llm_model_name"], "deepseek-flash")
        self.assertIsNone(record["robot_version_id"])
        self.assertNotIn("business_id", record)
        self.assertNotIn("work_order_id", record)
        self.assertNotIn("llm_param", record)
        self.assertNotIn("llm_prompt", record)
        self.assertNotIn("secret", str(record))
        self.assertNotIn("customer data", str(record))

    def test_sqlite_legacy_record_is_copied_without_business_schema(self):
        with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'migration.db'}")
            legacy_columns = ", ".join(
                f"{column} TEXT" for column in _PROCESS_RECORD_LEGACY_COLUMNS
            )
            with engine.begin() as conn:
                conn.execute(text(
                    f"CREATE TABLE process_record (id INTEGER PRIMARY KEY, {legacy_columns}, business_id TEXT)"
                ))
                conn.execute(text(
                    "INSERT INTO process_record (call_uuid, robot_code, business_id) "
                    "VALUES ('call-1', 'robot-1', 'business-1')"
                ))

                migrated = _prepare_sqlite_process_record(conn)
                for ddl in _SQLITE_DDL:
                    conn.execute(text(ddl))
                _copy_legacy_process_records(conn)

                record = conn.execute(text(
                    "SELECT call_uuid, robot_code, bot_mode FROM process_record"
                )).one()
                self.assertEqual(record.call_uuid, "call-1")
                self.assertEqual(record.robot_code, "robot-1")
                self.assertIsNone(record.bot_mode)
                columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(process_record)"))
                }
                self.assertNotIn("business_id", columns)
                self.assertEqual(conn.execute(text(
                    "SELECT COUNT(*) FROM process_record_legacy"
                )).scalar_one(), 1)

    def test_collector_inserts_into_new_sqlite_schema(self):
        collector = CallMetricsCollector(
            call_uuid="call-insert",
            robot_code="robot-1",
            bot_mode="standard",
            task_start_time=datetime.datetime.now(),
            asr_info={"provider": "deepgram", "model": "nova-2"},
            llm_info={"provider": "deepseek", "model": "deepseek-flash"},
            tts_info={"provider": "cartesia", "model": "sonic-2"},
        )
        collector.finalize(datetime.datetime.now())

        with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'insert.db'}")
            with engine.begin() as conn:
                for ddl in _SQLITE_DDL:
                    conn.execute(text(ddl))
                with patch("collection_agents.metrics.collector.MysqlClient.execute") as execute:
                    collector.save_to_db()
                sql, params = execute.call_args.args
                conn.execute(sql, params)

                record = conn.execute(text(
                    "SELECT call_uuid, bot_mode, llm_model_name FROM process_record"
                )).one()
                self.assertEqual(record.call_uuid, "call-insert")
                self.assertEqual(record.bot_mode, "standard")
                self.assertEqual(record.llm_model_name, "deepseek-flash")


if __name__ == "__main__":
    unittest.main()
