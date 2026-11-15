"""Read-only technical reports over Agent-owned call records."""

import base64
import datetime
import json
import math
from zoneinfo import ZoneInfo

from sqlalchemy import text

from collection_agents.storage.db import MysqlClient


_LATENCY_SAMPLE_LIMIT = 5000
_DAILY_LATENCY_SAMPLE_LIMIT = 1000
_MAX_DAILY_DAYS = 92
_REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
_SOURCE_EXPRESSION = """
    CASE
        WHEN call_source IS NOT NULL AND call_source <> '' THEN call_source
        WHEN call_uuid LIKE 'console-test-%' THEN 'console_voice_test'
        ELSE 'unclassified'
    END
"""
_JSON_FIELDS = (
    "technical_config", "asr_param_config", "asr_ttfb_list",
    "llm_ttfb_list", "tts_param_config", "tts_ttfb_list",
)
_DETAIL_NUMERIC_FIELDS = (
    "task_end_time", "asr_cost", "llm_cost", "tts_cost",
    "asr_total_transcribed_chars", "asr_total_audio_duration",
    "asr_connection_requests", "llm_total_generated_chars",
    "llm_prompt_tokens", "llm_completion_tokens", "llm_cache_read_tokens",
    "tts_total_output_duration", "tts_total_chars", "tts_connection_requests",
)
_TOTALS_SQL = """
    COUNT(*) AS total_records,
    COUNT(DISTINCT NULLIF(call_uuid, '')) AS logical_calls,
    COUNT(DISTINCT CASE WHEN is_voicemail = '1' OR is_voicemail = 1
                        THEN NULLIF(call_uuid, '') END) AS voicemail_calls,
    AVG(task_duration_sec) AS avg_duration_sec,
    SUM(task_duration_sec) AS total_duration_sec,
    SUM(asr_cost) AS asr_cost,
    SUM(llm_cost) AS llm_cost,
    SUM(tts_cost) AS tts_cost,
    SUM(total_cost) AS total_cost,
    COUNT(total_cost) AS cost_record_count,
    SUM(llm_retry_count) AS llm_retry_count,
    COUNT(llm_retry_count) AS llm_retry_records,
    SUM(asr_reconnect_count) AS asr_reconnect_count,
    COUNT(asr_reconnect_count) AS asr_reconnect_records,
    SUM(tts_reconnect_count) AS tts_reconnect_count,
    COUNT(tts_reconnect_count) AS tts_reconnect_records,
    SUM(llm_dialogue_turns) AS dialogue_turns,
    COUNT(llm_dialogue_turns) AS turns_record_count
"""


def _json_value(value, default):
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _isoformat(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_isoformat(value):
    if value is None:
        return None
    return _isoformat(datetime.datetime.fromtimestamp(
        value / 1000, tz=datetime.timezone.utc
    ))


def _parse_boundary(value, field):
    if value is None:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an ISO datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _epoch_ms(value):
    return int(value.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def _number(value, digits=2):
    return round(float(value), digits) if value is not None else None


def latency_stats(values):
    samples = sorted(
        float(value) for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    )
    if not samples:
        return {
            "count": 0, "avg_ms": None, "p50_ms": None,
            "p80_ms": None, "p95_ms": None, "max_ms": None,
        }

    def percentile(percent):
        return round(samples[max(0, math.ceil(len(samples) * percent) - 1)], 1)

    return {
        "count": len(samples),
        "avg_ms": round(sum(samples) / len(samples), 1),
        "p50_ms": percentile(0.50),
        "p80_ms": percentile(0.80),
        "p95_ms": percentile(0.95),
        "max_ms": round(samples[-1], 1),
    }


def _filters(start_at, end_at, robot_code, bot_mode, source, call_uuid):
    return {
        "start_at": _isoformat(start_at),
        "end_at": _isoformat(end_at),
        "robot_code": robot_code or "",
        "bot_mode": bot_mode or "",
        "source": source or "all",
        "call_uuid": call_uuid or "",
    }


def _encode_cursor(filters, row):
    value = {"filters": filters, "after": [row["task_start_time"], row["id"]]}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor, filters):
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        if value.get("filters") != filters:
            raise ValueError("cursor filters changed")
        started_at, row_id = value["after"]
        return {"cursor_started_at": int(started_at), "cursor_id": int(row_id)}
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise ValueError("Invalid report cursor") from error


def _safe_provider_config(value):
    if not isinstance(value, dict):
        return {}
    allowed = {"provider", "model", "locale", "sample_rate"}
    return {key: value[key] for key in allowed if key in value}


def _safe_technical_config(value):
    config = _json_value(value, {})
    if not isinstance(config, dict):
        config = {}
    pricing = config.get("pricing")
    safe_pricing = {}
    if isinstance(pricing, dict):
        for key in ("version", "currency", "estimated"):
            if key in pricing:
                safe_pricing[key] = pricing[key]
    return {
        **{name: _safe_provider_config(config.get(name)) for name in ("asr", "llm", "tts")},
        "pricing": safe_pricing,
    }


class CallReportRepository:
    def list_records(
        self, *, start_at=None, end_at=None, robot_code=None, bot_mode=None,
        source="all", call_uuid=None, cursor=None, limit=50,
    ):
        start_at = _parse_boundary(start_at, "start_at")
        end_at = _parse_boundary(end_at, "end_at")
        if start_at and end_at and start_at >= end_at:
            raise ValueError("start_at must be before end_at")
        filters = _filters(start_at, end_at, robot_code, bot_mode, source, call_uuid)
        where, params = self._where(start_at, end_at, robot_code, bot_mode, source, call_uuid)
        after = _decode_cursor(cursor, filters)
        page_params = dict(params)
        cursor_sql = ""
        if after:
            cursor_sql = (
                " AND (task_start_time < :cursor_started_at OR "
                "(task_start_time = :cursor_started_at AND id < :cursor_id))"
            )
            page_params.update(after)
        page_params["limit"] = limit + 1
        rows = MysqlClient.get_many(text(f"""
            SELECT id, call_uuid, call_source, claim_generation, bot_mode,
                   robot_code, robot_version_id, task_duration_sec,
                   task_start_time, is_voicemail, total_cost,
                   asr_model_name, llm_model_name, tts_model_name,
                   asr_ttfb_list, llm_ttfb_list, tts_ttfb_list,
                   llm_dialogue_turns, llm_retry_count,
                   asr_reconnect_count, tts_reconnect_count, created_at
            FROM call_record
            WHERE {where}{cursor_sql}
            ORDER BY task_start_time DESC, id DESC
            LIMIT :limit
        """), page_params)
        page = rows[:limit]
        next_cursor = _encode_cursor(filters, page[-1]) if len(rows) > limit and page else None
        return {
            "items": [self._list_item(row) for row in page],
            "summary": self._summary(where, params),
            "next_cursor": next_cursor,
        }

    def get_record(self, record_id):
        row = MysqlClient.get_one(text("""
            SELECT id, call_uuid, call_source, claim_generation, bot_mode,
                   robot_code, robot_version_id, task_duration_sec,
                   task_start_time, task_end_time, is_voicemail, technical_config,
                   asr_cost, llm_cost, tts_cost, total_cost,
                   asr_model_name, asr_param_config, asr_ttfb_list,
                   asr_total_transcribed_chars, asr_total_audio_duration,
                   asr_reconnect_count, asr_connection_requests,
                   llm_model_name, llm_retry_count, llm_dialogue_turns,
                   llm_ttfb_list, llm_total_generated_chars, llm_prompt_tokens,
                   llm_completion_tokens, llm_cache_read_tokens,
                   tts_model_name, tts_param_config, tts_ttfb_list,
                   tts_total_output_duration, tts_total_chars,
                   tts_reconnect_count, tts_connection_requests, created_at
            FROM call_record
            WHERE id = :record_id
        """), {"record_id": record_id})
        if row is None:
            return None
        result = self._list_item(row)
        for field in _JSON_FIELDS:
            default = [] if field.endswith("_list") else {}
            value = _json_value(row.get(field), default)
            if field == "technical_config":
                value = _safe_technical_config(row.get(field))
            elif field in {"asr_param_config", "tts_param_config"}:
                value = _safe_provider_config(value)
            result[field] = value
        for field in _DETAIL_NUMERIC_FIELDS:
            result[field] = row.get(field)
        result["task_start_time"] = _epoch_isoformat(row.get("task_start_time"))
        result["task_end_time"] = _epoch_isoformat(row.get("task_end_time"))
        result["record_id"] = row["id"]
        return result

    def get_summary(self, *, start_at=None, end_at=None, robot_code=None, bot_mode=None):
        start_at = _parse_boundary(start_at, "start_at")
        end_at = _parse_boundary(end_at, "end_at")
        if start_at and end_at and start_at >= end_at:
            raise ValueError("start_at must be before end_at")
        where, params = self._where(start_at, end_at, robot_code, bot_mode, "formal", None)
        return self._summary(where, params)

    def get_daily(
        self, *, start_at, end_at, robot_code=None, bot_mode=None
    ):
        start_at = _parse_boundary(start_at, "start_at")
        end_at = _parse_boundary(end_at, "end_at")
        if not start_at or not end_at or start_at >= end_at:
            raise ValueError("start_at and end_at must define a valid range")
        start_local = start_at.replace(tzinfo=datetime.timezone.utc).astimezone(_REPORT_TIMEZONE)
        end_local = end_at.replace(tzinfo=datetime.timezone.utc).astimezone(_REPORT_TIMEZONE)
        last_date = (end_local - datetime.timedelta(microseconds=1)).date()
        day_count = (last_date - start_local.date()).days + 1
        if day_count > _MAX_DAILY_DAYS:
            raise ValueError("daily reports support at most 92 natural days")
        ranges = []
        for offset in range(day_count):
            date = start_local.date() + datetime.timedelta(days=offset)
            local_start = datetime.datetime.combine(
                date, datetime.time.min, tzinfo=_REPORT_TIMEZONE
            )
            local_end = local_start + datetime.timedelta(days=1)
            day_start = max(start_at, local_start.astimezone(
                datetime.timezone.utc
            ).replace(tzinfo=None))
            day_end = min(end_at, local_end.astimezone(
                datetime.timezone.utc
            ).replace(tzinfo=None))
            ranges.append({"date": date.isoformat(), "start": day_start, "end": day_end})
        totals_by_day = self._daily_totals(
            ranges, start_at, end_at, robot_code, bot_mode
        )
        days = []
        for day in ranges:
            where, params = self._where(
                day["start"], day["end"], robot_code, bot_mode, "formal", None
            )
            item = self._summary_from_totals(
                totals_by_day.get(day["date"], {}), where, params,
                sample_limit=_DAILY_LATENCY_SAMPLE_LIMIT,
            )
            item["date"] = day["date"]
            days.append(item)
        return {
            "items": days,
            "start_at": _isoformat(start_at),
            "end_at": _isoformat(end_at),
        }

    def get_facets(self):
        robots = MysqlClient.get_many(text("""
            SELECT DISTINCT robot_code AS value FROM call_record
            WHERE robot_code IS NOT NULL AND robot_code <> '' ORDER BY robot_code
        """))
        modes = MysqlClient.get_many(text("""
            SELECT DISTINCT bot_mode AS value FROM call_record
            WHERE bot_mode IS NOT NULL AND bot_mode <> '' ORDER BY bot_mode
        """))
        actual_modes = {row["value"] for row in modes}
        return {
            "robot_codes": [row["value"] for row in robots],
            "bot_modes": sorted(actual_modes | {"standard", "realtime", "copilot"}),
        }

    @staticmethod
    def _where(start_at, end_at, robot_code, bot_mode, source, call_uuid):
        conditions = [] if call_uuid else ["task_start_time IS NOT NULL"]
        params = {}
        if start_at is not None:
            conditions.append("task_start_time >= :start_ms")
            params["start_ms"] = _epoch_ms(start_at)
        if end_at is not None:
            conditions.append("task_start_time < :end_ms")
            params["end_ms"] = _epoch_ms(end_at)
        if robot_code:
            conditions.append("robot_code = :robot_code")
            params["robot_code"] = robot_code
        if bot_mode:
            conditions.append("bot_mode = :bot_mode")
            params["bot_mode"] = bot_mode
        if source == "formal":
            conditions.append(f"{_SOURCE_EXPRESSION} <> 'mock_call'")
        elif source and source != "all":
            conditions.append(f"{_SOURCE_EXPRESSION} = :source")
            params["source"] = source
        if call_uuid:
            conditions.append("call_uuid = :call_uuid")
            params["call_uuid"] = call_uuid
        return " AND ".join(conditions or ["1 = 1"]), params

    def _daily_totals(self, ranges, start_at, end_at, robot_code, bot_mode):
        where, params = self._where(start_at, end_at, robot_code, bot_mode, "formal", None)
        cases = []
        for index, day in enumerate(ranges):
            start_key = f"day_{index}_start"
            end_key = f"day_{index}_end"
            date_key = f"day_{index}_date"
            cases.append(
                f"WHEN task_start_time >= :{start_key} "
                f"AND task_start_time < :{end_key} THEN :{date_key}"
            )
            params[start_key] = _epoch_ms(day["start"])
            params[end_key] = _epoch_ms(day["end"])
            params[date_key] = day["date"]
        case_sql = "CASE " + " ".join(cases) + " ELSE NULL END"
        rows = MysqlClient.get_many(text(f"""
            SELECT {case_sql} AS report_date, {_TOTALS_SQL}
            FROM call_record WHERE {where} GROUP BY report_date
        """), params)
        return {row["report_date"]: row for row in rows}

    def _summary(self, where, params, sample_limit=_LATENCY_SAMPLE_LIMIT):
        totals = MysqlClient.get_one(text(f"""
            SELECT {_TOTALS_SQL} FROM call_record WHERE {where}
        """), params) or {}
        return self._summary_from_totals(totals, where, params, sample_limit)

    def _summary_from_totals(self, totals, where, params, sample_limit):
        sample_params = dict(params, latency_limit=sample_limit)
        samples = MysqlClient.get_many(text(f"""
            SELECT asr_ttfb_list, llm_ttfb_list, tts_ttfb_list
            FROM call_record WHERE {where}
            ORDER BY task_start_time DESC, id DESC LIMIT :latency_limit
        """), sample_params)
        latency = {name: [] for name in ("asr", "llm", "tts")}
        for row in samples:
            for name in latency:
                values = _json_value(row.get(f"{name}_ttfb_list"), [])
                if isinstance(values, list):
                    latency[name].extend(values)
        total_records = int(totals.get("total_records") or 0)
        logical_calls = int(totals.get("logical_calls") or 0)
        voicemail_calls = int(totals.get("voicemail_calls") or 0)
        turns_records = int(totals.get("turns_record_count") or 0)
        return {
            "total_records": total_records,
            "logical_calls": logical_calls,
            "voicemail_calls": voicemail_calls,
            "voicemail_rate": (
                round(voicemail_calls / logical_calls * 100, 1) if logical_calls else None
            ),
            "avg_duration_sec": _number(totals.get("avg_duration_sec")),
            "total_duration_sec": _number(totals.get("total_duration_sec")),
            "avg_dialogue_turns": (
                _number(float(totals.get("dialogue_turns") or 0) / turns_records, 1)
                if turns_records else None
            ),
            "turns_record_count": turns_records,
            "missing_turns_records": total_records - turns_records,
            "asr_cost": _number(totals.get("asr_cost"), 8),
            "llm_cost": _number(totals.get("llm_cost"), 8),
            "tts_cost": _number(totals.get("tts_cost"), 8),
            "total_cost": _number(totals.get("total_cost"), 8),
            "cost_record_count": int(totals.get("cost_record_count") or 0),
            "missing_cost_records": total_records - int(totals.get("cost_record_count") or 0),
            "cost_currency": "USD",
            "cost_estimated": True,
            "llm_retry_count": int(totals.get("llm_retry_count") or 0),
            "missing_llm_retry_records": total_records - int(totals.get("llm_retry_records") or 0),
            "asr_reconnect_count": int(totals.get("asr_reconnect_count") or 0),
            "missing_asr_reconnect_records": total_records - int(totals.get("asr_reconnect_records") or 0),
            "tts_reconnect_count": int(totals.get("tts_reconnect_count") or 0),
            "missing_tts_reconnect_records": total_records - int(totals.get("tts_reconnect_records") or 0),
            "latency": {name: latency_stats(values) for name, values in latency.items()},
            "latency_sample_records": len(samples),
            "latency_total_records": total_records,
            "latency_sample_limit": sample_limit,
            "latency_truncated": total_records > len(samples),
        }

    @staticmethod
    def _list_item(row):
        source = row.get("call_source")
        if not source:
            source = (
                "console_voice_test"
                if str(row.get("call_uuid") or "").startswith("console-test-")
                else "unclassified"
            )
        return {
            "record_id": row["id"],
            "call_uuid": row.get("call_uuid") or "",
            "call_source": source,
            "claim_generation": row.get("claim_generation"),
            "bot_mode": row.get("bot_mode") or "standard",
            "robot_code": row.get("robot_code") or "",
            "robot_version_id": row.get("robot_version_id"),
            "task_start_time": _epoch_isoformat(row.get("task_start_time")),
            "task_duration_sec": row.get("task_duration_sec"),
            "is_voicemail": str(row.get("is_voicemail") or "").lower() in {"1", "true"},
            "total_cost": row.get("total_cost"),
            "asr_model_name": row.get("asr_model_name") or "",
            "llm_model_name": row.get("llm_model_name") or "",
            "tts_model_name": row.get("tts_model_name") or "",
            "llm_dialogue_turns": row.get("llm_dialogue_turns"),
            "llm_retry_count": row.get("llm_retry_count"),
            "asr_reconnect_count": row.get("asr_reconnect_count"),
            "tts_reconnect_count": row.get("tts_reconnect_count"),
            "created_at": _isoformat(row.get("created_at")),
            "latency": {
                name: latency_stats(_json_value(row.get(f"{name}_ttfb_list"), []))
                for name in ("asr", "llm", "tts")
            },
        }
