import datetime
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from collection_agents.server.report_routes import router
from collection_agents.storage.db import _SQLITE_DDL, _migrate_sqlite_call_record
from collection_agents.storage.reports import CallReportRepository


START = int(datetime.datetime(2026, 11, 14, tzinfo=datetime.timezone.utc).timestamp() * 1000)
HOUR = 60 * 60 * 1000


class CallReportRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.sessions = sessionmaker(self.engine)
        with self.engine.begin() as conn:
            for ddl in _SQLITE_DDL:
                conn.execute(text(ddl))
            _migrate_sqlite_call_record(conn)
        self.rows = [
            (1, "call-1", "fastcall_outbound", 1, START, 10, 0.1, "[100, 200]"),
            (2, "call-1", "fastcall_outbound", 2, START + HOUR, 20, 0.2, "[300, -5, null]"),
            (3, "console-test-1", None, None, START + 2 * HOUR, 30, 0.3, "[400]"),
            (4, "legacy-1", None, None, START + 3 * HOUR, 40, 0.4, "[500]"),
            (5, "null-start", "fastcall_outbound", 1, None, 50, 0.5, "[600]"),
        ]
        with self.engine.begin() as conn:
            for row_id, call_uuid, source, generation, started, duration, cost, asr in self.rows:
                conn.execute(text("""
                    INSERT INTO call_record (
                        id, call_uuid, call_source, claim_generation, robot_code, bot_mode,
                        task_start_time, task_duration_sec, total_cost, asr_cost, llm_cost,
                        tts_cost, asr_ttfb_list, llm_ttfb_list, tts_ttfb_list,
                        llm_retry_count, asr_reconnect_count, tts_reconnect_count,
                        technical_config, asr_param_config, tts_param_config, created_at
                    ) VALUES (
                        :id, :call_uuid, :source, :generation, 'robot-1', 'standard',
                        :started, :duration, :cost, 0.01, 0.02, 0.03,
                        :asr, '[300]', '[400]', 1, 2, 3,
                        :technical, '{}', '{}', CURRENT_TIMESTAMP
                    )
                """), {
                    "id": row_id, "call_uuid": call_uuid, "source": source,
                    "generation": generation, "started": started, "duration": duration,
                    "cost": cost, "asr": asr,
                    "technical": '{"asr":{"provider":"deepgram","api_key":"secret"},'
                                 '"pricing":{"version":"v1","currency":"USD","estimated":true}}',
                })
        self.client = CallReportRepository()
        self.patch_many = patch(
            "collection_agents.storage.reports.MysqlClient.get_many", side_effect=self.get_many
        )
        self.patch_one = patch(
            "collection_agents.storage.reports.MysqlClient.get_one", side_effect=self.get_one
        )
        self.patch_many.start()
        self.patch_one.start()

    def tearDown(self):
        self.patch_one.stop()
        self.patch_many.stop()
        self.engine.dispose()

    def get_many(self, statement, params):
        with self.sessions() as session:
            result = session.execute(statement, params)
            return [dict(zip(result.keys(), row)) for row in result.fetchall()]

    def get_one(self, statement, params):
        with self.sessions() as session:
            result = session.execute(statement, params)
            row = result.fetchone()
            return dict(zip(result.keys(), row)) if row else None

    def test_default_source_summary_and_stable_cursor(self):
        first = self.client.list_records(limit=1, source="fastcall_outbound")
        self.assertEqual(first["items"][0]["record_id"], 2)
        self.assertEqual(first["summary"]["total_records"], 2)
        self.assertEqual(first["summary"]["avg_duration_sec"], 15)
        self.assertEqual(first["summary"]["latency"]["asr"]["count"], 3)
        self.assertEqual(first["summary"]["latency"]["asr"]["p95_ms"], 300)
        self.assertEqual(first["summary"]["missing_cost_records"], 0)
        second = self.client.list_records(
            limit=1, cursor=first["next_cursor"], source="fastcall_outbound"
        )
        self.assertEqual(second["items"][0]["record_id"], 1)
        self.assertIsNone(second["next_cursor"])
        with self.assertRaises(ValueError):
            self.client.list_records(limit=1, cursor=first["next_cursor"], source="all")

    def test_call_panel_returns_all_sources_and_null_start_only_for_exact_call(self):
        records = self.client.list_records(source="all", call_uuid="call-1")
        self.assertEqual([item["record_id"] for item in records["items"]], [2, 1])
        self.assertEqual(records["summary"]["total_records"], 2)
        legacy = self.client.list_records(source="all", call_uuid="legacy-1")
        self.assertEqual(legacy["items"][0]["call_source"], "unclassified")
        voice = self.client.list_records(source="console_voice_test")
        self.assertEqual([item["record_id"] for item in voice["items"]], [3])
        self.assertEqual(self.client.list_records(source="unclassified")["summary"]["total_records"], 1)
        exact_null = self.client.list_records(source="all", call_uuid="null-start")
        self.assertEqual(exact_null["summary"]["total_records"], 1)
        self.assertNotIn(5, [item["record_id"] for item in self.client.list_records(source="all")["items"]])

    def test_absolute_boundaries_and_empty_summary(self):
        result = self.client.list_records(
            start_at="2026-11-14T01:00:00Z", end_at="2026-11-14T02:00:00Z"
        )
        self.assertEqual(result["summary"]["total_records"], 1)
        self.assertEqual(result["items"][0]["record_id"], 2)
        empty = self.client.list_records(start_at="2027-01-01T00:00:00Z")
        self.assertEqual(empty["summary"]["total_records"], 0)
        self.assertIsNone(empty["summary"]["avg_duration_sec"])
        self.assertIsNone(empty["summary"]["total_cost"])
        self.assertFalse(empty["summary"]["latency_truncated"])
        with self.assertRaises(ValueError):
            self.client.list_records(start_at="2026-11-14T00:00:00")

    def test_summary_reports_missing_retry_and_reconnect_counts(self):
        with self.engine.begin() as conn:
            conn.execute(text(
                "UPDATE call_record SET llm_retry_count=NULL, "
                "asr_reconnect_count=NULL, tts_reconnect_count=NULL"
            ))
        summary = self.client.list_records(source="fastcall_outbound")["summary"]
        self.assertEqual(summary["missing_llm_retry_records"], 2)
        self.assertEqual(summary["missing_asr_reconnect_records"], 2)
        self.assertEqual(summary["missing_tts_reconnect_records"], 2)

    def test_detail_uses_record_id_and_sanitizes_configuration(self):
        detail = self.client.get_record(1)
        self.assertEqual(detail["record_id"], 1)
        self.assertEqual(detail["claim_generation"], 1)
        self.assertEqual(detail["technical_config"]["asr"], {"provider": "deepgram"})
        self.assertNotIn("secret", str(detail))
        self.assertIsNone(self.client.get_record(999))

    def test_existing_sqlite_table_is_upgraded(self):
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE call_record (id INTEGER PRIMARY KEY, call_uuid TEXT, task_start_time INTEGER)"
            ))
            _migrate_sqlite_call_record(conn)
            columns = {row[1] for row in conn.execute(text("PRAGMA table_info(call_record)"))}
            self.assertIn("call_source", columns)
            self.assertIn("claim_generation", columns)
        engine.dispose()


class CallReportRouteTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def test_authentication_and_detail_not_found(self):
        with patch.dict("os.environ", {"FASTCALL_AGENT_TOKEN": "agent-token"}):
            self.assertEqual(self.client.get("/internal/v1/call-records").status_code, 401)
            with patch(
                "collection_agents.server.report_routes.CallReportRepository.list_records",
                return_value={"items": [], "summary": {}, "next_cursor": None},
            ) as list_records:
                response = self.client.get(
                    "/internal/v1/call-records",
                    headers={"Authorization": "Bearer agent-token"},
                )
                self.assertEqual(response.status_code, 200, response.text)
                list_records.assert_called_once()
            with patch(
                "collection_agents.server.report_routes.CallReportRepository.get_record",
                return_value=None,
            ):
                missing = self.client.get(
                    "/internal/v1/call-records/99",
                    headers={"Authorization": "Bearer agent-token"},
                )
                self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
