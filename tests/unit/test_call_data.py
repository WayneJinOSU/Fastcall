import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, text

from collection_agents.infra.paths import DATA_DIR
from collection_agents.notes.call_notes import _save_analysis_result
from collection_agents.storage.call_data import (
    get_latest_call_analysis,
    save_call_analysis,
    save_call_transcript,
)
from collection_agents.storage.db import _SQLITE_DDL


class CallDataPersistenceTests(unittest.TestCase):
    def test_collection_note_is_saved_to_generic_analysis_container(self):
        result = {"collectionResult": "yes_promise_today"}
        with patch(
            "collection_agents.notes.call_notes.save_call_analysis"
        ) as save_analysis:
            _save_analysis_result(
                call_uuid="call-1",
                analysis_type="collection_notes",
                result=result,
                success=True,
            )

        save_analysis.assert_called_once_with(
            call_uuid="call-1",
            analysis_type="collection_notes",
            result=result,
            status="success",
            schema_version="1",
            analyzer_version="local-v1",
            error_message=None,
        )

    def test_transcript_is_saved_in_its_own_table(self):
        with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'transcript.db'}")
            with engine.begin() as conn:
                for ddl in _SQLITE_DDL:
                    conn.execute(text(ddl))

            with patch("collection_agents.storage.call_data.MysqlClient.execute") as execute:
                save_call_transcript(
                    call_uuid="call-1",
                    language="es",
                    messages=[{"role": "user", "content": "hola"}],
                )

            sql, params = execute.call_args.args
            with engine.begin() as conn:
                conn.execute(sql, params)
                row = conn.execute(text(
                    "SELECT call_uuid, language, messages_json FROM call_transcript"
                )).one()

            self.assertEqual(row.call_uuid, "call-1")
            self.assertEqual(row.language, "es")
            self.assertEqual(json.loads(row.messages_json)[0]["content"], "hola")

    def test_analysis_result_is_saved_as_generic_json(self):
        with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'analysis.db'}")
            with engine.begin() as conn:
                for ddl in _SQLITE_DDL:
                    conn.execute(text(ddl))

            result = {
                "repaymentWish": "yes",
                "collectionResult": "yes_promise_today",
                "remark": "customer promised to pay",
            }
            with patch("collection_agents.storage.call_data.MysqlClient.execute") as execute:
                save_call_analysis(
                    call_uuid="call-1",
                    analysis_type="collection_note",
                    result=result,
                    analyzer_version="local-v1",
                )

            sql, params = execute.call_args.args
            with engine.begin() as conn:
                conn.execute(sql, params)
                row = conn.execute(text(
                    "SELECT status, result_json FROM call_analysis"
                )).one()

            self.assertEqual(row.status, "success")
            self.assertEqual(json.loads(row.result_json), result)

    def test_latest_analysis_is_selected_by_type(self):
        with tempfile.TemporaryDirectory(dir=DATA_DIR) as directory:
            engine = create_engine(f"sqlite:///{Path(directory) / 'latest.db'}")
            with engine.begin() as conn:
                for ddl in _SQLITE_DDL:
                    conn.execute(text(ddl))
                conn.execute(text("""
                    INSERT INTO call_analysis
                        (call_uuid, analysis_type, status, result_json,
                         created_at, updated_at)
                    VALUES
                        ('call-1', 'collection_note', 'failed', '{}',
                         '2026-11-10 10:00:00', '2026-11-10 10:00:00'),
                        ('call-1', 'collection_note', 'success', '{"remark":"new"}',
                         '2026-11-10 10:01:00', '2026-11-10 10:01:00'),
                        ('call-1', 'summary', 'success', '{"summary":"other"}',
                         '2026-11-10 10:02:00', '2026-11-10 10:02:00')
                """))

            def get_one(sql, params):
                with engine.connect() as conn:
                    row = conn.execute(sql, params).mappings().one()
                    return dict(row)

            with patch(
                "collection_agents.storage.call_data.MysqlClient.get_one",
                side_effect=get_one,
            ):
                row = get_latest_call_analysis("call-1", "collection_note")

            self.assertEqual(row["status"], "success")
            self.assertEqual(json.loads(row["result_json"]), {"remark": "new"})


if __name__ == "__main__":
    unittest.main()
