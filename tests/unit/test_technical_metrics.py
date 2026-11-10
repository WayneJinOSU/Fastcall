import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from collection_agents.infra.paths import DATA_DIR
from collection_agents.metrics.collector import CallMetricsCollector
from collection_agents.storage.db import (
    _CALL_RECORD_COLUMNS,
    _SQLITE_DDL,
    _copy_context_log_rows,
    _copy_process_record_rows,
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

    def test_sqlite_process_record_is_copied_without_business_schema(self):
        with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'migration.db'}")
            legacy_columns = ", ".join(
                f"{column} TEXT" for column in _CALL_RECORD_COLUMNS
            )
            with engine.begin() as conn:
                conn.execute(text(
                    f"CREATE TABLE process_record (id INTEGER PRIMARY KEY, {legacy_columns}, business_id TEXT)"
                ))
                conn.execute(text(
                    "INSERT INTO process_record (call_uuid, robot_code, business_id) "
                    "VALUES ('call-1', 'robot-1', 'business-1')"
                ))

                for ddl in _SQLITE_DDL:
                    conn.execute(text(ddl))
                self.assertEqual(_copy_process_record_rows(conn), 1)
                self.assertEqual(_copy_process_record_rows(conn), 0)

                record = conn.execute(text(
                    "SELECT call_uuid, robot_code, bot_mode, created_at FROM call_record"
                )).one()
                self.assertEqual(record.call_uuid, "call-1")
                self.assertEqual(record.robot_code, "robot-1")
                self.assertIsNone(record.bot_mode)
                self.assertIsNotNone(record.created_at)
                columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(call_record)"))
                }
                self.assertNotIn("business_id", columns)

    def test_sqlite_context_log_is_copied_to_generic_transcript(self):
        with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'transcript.db'}")
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE context_log (
                        id INTEGER PRIMARY KEY,
                        user_name TEXT,
                        business_id TEXT,
                        remaining_amount TEXT,
                        context TEXT,
                        app_type TEXT,
                        country TEXT,
                        ctime1 INTEGER,
                        uuid TEXT
                    )
                """))
                conn.execute(text("""
                    INSERT INTO context_log
                        (user_name, business_id, remaining_amount, context, app_type,
                         country, ctime1, uuid)
                    VALUES
                        ('customer', 'business-1', '100', '[{"role":"user","content":"hi"}]',
                         'app', 'mx', 1760000000000, 'call-1')
                """))

                for ddl in _SQLITE_DDL:
                    conn.execute(text(ddl))
                self.assertEqual(_copy_context_log_rows(conn), 1)
                self.assertEqual(_copy_context_log_rows(conn), 0)

                record = conn.execute(text(
                    "SELECT call_uuid, messages_json FROM call_transcript"
                )).one()
                self.assertEqual(record.call_uuid, "call-1")
                self.assertIn('"content":"hi"', record.messages_json)
                columns = {
                    row[1]
                    for row in conn.execute(text("PRAGMA table_info(call_transcript)"))
                }
                self.assertEqual(
                    columns,
                    {"id", "call_uuid", "language", "messages_json", "created_at"},
                )

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
                    "SELECT call_uuid, bot_mode, llm_model_name, created_at FROM call_record"
                )).one()
                self.assertEqual(record.call_uuid, "call-insert")
                self.assertEqual(record.bot_mode, "standard")
                self.assertEqual(record.llm_model_name, "deepseek-flash")
                self.assertIsNotNone(record.created_at)


if __name__ == "__main__":
    unittest.main()
