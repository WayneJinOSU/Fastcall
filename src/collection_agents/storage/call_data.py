"""Persistence helpers for call transcripts and generic analysis results."""

import datetime
import json

from sqlalchemy import text

from collection_agents.storage.db import MysqlClient


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _json_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def save_call_transcript(
    *,
    call_uuid: str,
    messages,
    language: str = "",
    created_at: datetime.datetime | None = None,
) -> None:
    """Persist the conversation independently from call metrics."""
    MysqlClient.execute(
        text("""
            INSERT INTO call_transcript
                (call_uuid, language, messages_json, created_at)
            VALUES
                (:call_uuid, :language, :messages_json, :created_at)
        """),
        {
            "call_uuid": call_uuid or "",
            "language": language or "",
            "messages_json": _json_text(messages or []),
            "created_at": created_at or _utcnow(),
        },
    )


def save_call_analysis(
    *,
    call_uuid: str,
    analysis_type: str,
    result,
    status: str = "success",
    schema_version: str = "1",
    analyzer_version: str = "",
    model_name: str = "",
    error_message: str | None = None,
) -> None:
    """Persist one analysis attempt without coupling business fields into call_record."""
    now = _utcnow()
    MysqlClient.execute(
        text("""
            INSERT INTO call_analysis
                (call_uuid, analysis_type, status, schema_version,
                 analyzer_version, model_name, result_json, error_message,
                 created_at, updated_at)
            VALUES
                (:call_uuid, :analysis_type, :status, :schema_version,
                 :analyzer_version, :model_name, :result_json, :error_message,
                 :created_at, :updated_at)
        """),
        {
            "call_uuid": call_uuid or "",
            "analysis_type": analysis_type,
            "status": status,
            "schema_version": schema_version or "",
            "analyzer_version": analyzer_version or "",
            "model_name": model_name or "",
            "result_json": _json_text(result or {}),
            "error_message": error_message,
            "created_at": now,
            "updated_at": now,
        },
    )


def get_latest_call_analysis(call_uuid: str, analysis_type: str):
    """Return the newest analysis attempt for a call and analysis type."""
    return MysqlClient.get_one(
        text("""
            SELECT call_uuid, analysis_type, status, schema_version,
                   analyzer_version, model_name, result_json, error_message,
                   created_at, updated_at
            FROM call_analysis
            WHERE call_uuid = :call_uuid AND analysis_type = :analysis_type
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """),
        {"call_uuid": call_uuid, "analysis_type": analysis_type},
    )
