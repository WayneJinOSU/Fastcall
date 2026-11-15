import datetime
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from collection_agents.metrics.collector import CallMetricsCollector
from collection_agents.storage.db import _SQLITE_DDL, _migrate_sqlite_call_record
from collection_agents.storage.reports import CallReportRepository, latency_stats


class CallAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        self.sessions = sessionmaker(self.engine)
        with self.engine.begin() as conn:
            for ddl in _SQLITE_DDL:
                conn.execute(text(ddl))
            _migrate_sqlite_call_record(conn)
        rows = [
            (1, "call-a", 1, "2026-11-14T02:00:00Z", 10, 2, 0.1, "[100,200,300,400]"),
            (2, "call-a", 0, "2026-11-14T03:00:00Z", 20, 4, 0.2, "[500]"),
            (3, "call-b", 1, "2026-11-15T01:00:00Z", 30, None, None, "[]"),
        ]
        with self.engine.begin() as conn:
            for row_id, call_id, voicemail, started, duration, turns, cost, asr in rows:
                started_ms = int(datetime.datetime.fromisoformat(
                    started.replace("Z", "+00:00")
                ).timestamp() * 1000)
                conn.execute(text("""
                    INSERT INTO call_record (
                        id, call_uuid, call_source, claim_generation, robot_code, bot_mode,
                        task_start_time, task_duration_sec, llm_dialogue_turns, total_cost,
                        asr_ttfb_list, llm_ttfb_list, tts_ttfb_list, is_voicemail,
                        llm_retry_count, asr_reconnect_count, tts_reconnect_count, created_at
                    ) VALUES (
                        :id, :call_id, 'fastcall_outbound', 1, 'robot-1', 'standard',
                        :started, :duration, :turns, :cost, :asr, '[200,400]', '[100]',
                        :voicemail, 0, 0, 0, CURRENT_TIMESTAMP
                    )
                """), {
                    "id": row_id, "call_id": call_id, "started": started_ms,
                    "duration": duration, "turns": turns, "cost": cost,
                    "asr": asr, "voicemail": voicemail,
                })
        self.repository = CallReportRepository()
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

    def get_many(self, statement, params=None):
        with self.sessions() as session:
            result = session.execute(statement, params or {})
            return [dict(zip(result.keys(), row)) for row in result.fetchall()]

    def get_one(self, statement, params):
        with self.sessions() as session:
            result = session.execute(statement, params)
            row = result.fetchone()
            return dict(zip(result.keys(), row)) if row else None

    def test_summary_uses_logical_calls_and_full_latency_distribution(self):
        summary = self.repository.get_summary(
            start_at="2026-11-13T16:00:00Z", end_at="2026-11-16T16:00:00Z"
        )
        self.assertEqual(summary["total_records"], 3)
        self.assertEqual(summary["logical_calls"], 2)
        self.assertEqual(summary["voicemail_calls"], 2)
        self.assertEqual(summary["voicemail_rate"], 100)
        self.assertEqual(summary["avg_duration_sec"], 20)
        self.assertEqual(summary["avg_dialogue_turns"], 3)
        self.assertEqual(summary["missing_turns_records"], 1)
        self.assertEqual(summary["missing_cost_records"], 1)
        self.assertEqual(summary["latency"]["asr"]["avg_ms"], 300)
        self.assertEqual(summary["latency"]["asr"]["p50_ms"], 300)
        self.assertEqual(summary["latency"]["asr"]["p80_ms"], 400)
        self.assertEqual(summary["latency"]["asr"]["p95_ms"], 500)

    def test_daily_includes_empty_days_and_uses_per_day_samples(self):
        with patch(
            "collection_agents.storage.reports.MysqlClient.get_many", wraps=self.get_many
        ) as daily_queries:
            report = self.repository.get_daily(
                start_at="2026-11-13T16:00:00Z", end_at="2026-11-16T16:00:00Z"
            )
        aggregate_queries = [
            call for call in daily_queries.call_args_list
            if "GROUP BY report_date" in str(call.args[0])
        ]
        self.assertEqual(len(aggregate_queries), 1)
        self.assertEqual([item["date"] for item in report["items"]], [
            "2026-11-14", "2026-11-15", "2026-11-16",
        ])
        first, second, empty = report["items"]
        self.assertEqual(first["logical_calls"], 1)
        self.assertEqual(first["voicemail_rate"], 100)
        self.assertEqual(first["avg_dialogue_turns"], 3)
        self.assertEqual(first["latency"]["asr"]["p95_ms"], 500)
        self.assertEqual(second["logical_calls"], 1)
        self.assertEqual(second["total_records"], 1)
        self.assertIsNone(second["total_cost"])
        self.assertEqual(empty["total_records"], 0)
        self.assertIsNone(empty["voicemail_rate"])
        self.assertIsNone(empty["avg_dialogue_turns"])
        self.assertIsNone(empty["latency"]["asr"]["p50_ms"])
        self.assertFalse(empty["latency_truncated"])
        with self.assertRaises(ValueError):
            self.repository.get_daily(
                start_at="2026-01-01T00:00:00Z", end_at="2026-06-01T00:00:00Z"
            )

    def test_facets_come_from_agent_records(self):
        self.assertEqual(self.repository.get_facets(), {
            "robot_codes": ["robot-1"],
            "bot_modes": ["copilot", "realtime", "standard"],
        })

    def test_console_voice_test_does_not_write_metrics(self):
        collector = CallMetricsCollector(
            call_uuid="console-test-1", robot_code="robot-1", bot_mode="standard",
            task_start_time=datetime.datetime.now(), asr_info={}, llm_info={}, tts_info={},
            call_source="console_voice_test",
        )
        with patch("collection_agents.metrics.collector.MysqlClient.execute") as execute:
            collector.save_to_db()
            execute.assert_not_called()

    def test_latency_empty_and_single_sample(self):
        empty = latency_stats([])
        self.assertIsNone(empty["p50_ms"])
        single = latency_stats([120])
        self.assertEqual(single["avg_ms"], 120)
        self.assertEqual(single["p50_ms"], 120)
        self.assertEqual(single["p80_ms"], 120)
        self.assertEqual(single["p95_ms"], 120)


if __name__ == "__main__":
    unittest.main()
