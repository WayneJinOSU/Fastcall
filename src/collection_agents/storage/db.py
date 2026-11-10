"""存储抽象层：默认 SQLite（零配置自部署），MySQL 作为可选后端。

通过环境变量选择后端：

- 不配置：默认 ``sqlite:///./data/collection_agents.db``，表自动创建，开箱即用。
- ``DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname``：使用 MySQL
  （表由运维侧预先创建，本模块不会在 MySQL 上自动建表）。

对调用方暴露与历史 MysqlClient 相同的接口（execute / get_one / get_many），
``my_utils.mysql.MysqlClient`` 是本模块 DBClient 的兼容别名。
"""

import logging
import os

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from collection_agents.infra.paths import DATA_DIR

LOGGER = logging.getLogger(__name__)

_DEFAULT_SQLITE_URL = f"sqlite:///{DATA_DIR / 'collection_agents.db'}"

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip() or _DEFAULT_SQLITE_URL
IS_SQLITE = DATABASE_URL.startswith("sqlite")


def _create_engine():
    if IS_SQLITE:
        db_path = DATABASE_URL.replace("sqlite:///", "", 1)
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        return create_engine(DATABASE_URL, pool_pre_ping=True)
    return create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800)


engine = _create_engine()
Session = sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# SQLite 自动建表（MySQL 由运维建表，此处不处理）
# ---------------------------------------------------------------------------

_CALL_RECORD_COLUMNS = (
    "call_uuid", "bot_mode", "robot_code", "robot_version_id",
    "task_duration_sec", "task_start_time", "task_end_time",
    "is_voicemail", "technical_config", "asr_cost", "llm_cost", "tts_cost",
    "total_cost", "asr_model_name", "asr_param_config", "asr_ttfb_list",
    "asr_total_transcribed_chars", "asr_total_audio_duration",
    "asr_reconnect_count", "asr_connection_requests", "llm_model_name",
    "llm_retry_count", "llm_dialogue_turns", "llm_ttfb_list",
    "llm_total_generated_chars", "llm_prompt_tokens", "llm_completion_tokens",
    "llm_cache_read_tokens", "tts_model_name", "tts_param_config",
    "tts_ttfb_list", "tts_total_output_duration", "tts_total_chars",
    "tts_reconnect_count", "tts_connection_requests",
)

_SQLITE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS call_record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_uuid TEXT,
        bot_mode TEXT,
        robot_code TEXT,
        robot_version_id TEXT,
        task_duration_sec REAL,
        task_start_time INTEGER,
        task_end_time INTEGER,
        is_voicemail TEXT,
        technical_config TEXT,
        asr_cost REAL,
        llm_cost REAL,
        tts_cost REAL,
        total_cost REAL,
        asr_model_name TEXT,
        asr_param_config TEXT,
        asr_ttfb_list TEXT,
        asr_total_transcribed_chars REAL,
        asr_total_audio_duration REAL,
        asr_reconnect_count INTEGER,
        asr_connection_requests INTEGER,
        llm_model_name TEXT,
        llm_retry_count INTEGER,
        llm_dialogue_turns INTEGER,
        llm_ttfb_list TEXT,
        llm_total_generated_chars REAL,
        llm_prompt_tokens INTEGER,
        llm_completion_tokens INTEGER,
        llm_cache_read_tokens INTEGER,
        tts_model_name TEXT,
        tts_param_config TEXT,
        tts_ttfb_list TEXT,
        tts_total_output_duration REAL,
        tts_total_chars REAL,
        tts_reconnect_count INTEGER,
        tts_connection_requests INTEGER,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_call_record_call_uuid
    ON call_record(call_uuid)
    """,
    """
    CREATE TABLE IF NOT EXISTS call_transcript (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_uuid TEXT,
        language TEXT,
        messages_json TEXT NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_call_transcript_call_uuid
    ON call_transcript(call_uuid)
    """,
    """
    CREATE TABLE IF NOT EXISTS call_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_uuid TEXT,
        analysis_type TEXT NOT NULL,
        status TEXT NOT NULL,
        schema_version TEXT,
        analyzer_version TEXT,
        model_name TEXT,
        result_json TEXT,
        error_message TEXT,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_call_analysis_call_type_time
    ON call_analysis(call_uuid, analysis_type, created_at)
    """,
]

_ASR_SHADOW_COMPARE_DDL = """
    CREATE TABLE IF NOT EXISTS asr_shadow_compare (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_uuid TEXT,
        robot_code TEXT,
        language TEXT,
        vosk_model TEXT,
        deepgram_model TEXT,
        vosk_text TEXT,
        deepgram_text TEXT,
        vosk_segments TEXT,
        deepgram_segments TEXT,
        ctime1 INTEGER
    )
"""


def _sqlite_table_columns(conn, table_name: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    }


def _copy_process_record_rows(conn) -> int:
    """Copy legacy metrics into call_record without changing or deleting the source."""
    source_columns = _sqlite_table_columns(conn, "process_record")
    if not source_columns:
        return 0

    columns = [column for column in _CALL_RECORD_COLUMNS if column in source_columns]
    if "call_uuid" not in columns:
        return 0

    column_sql = ", ".join(columns)
    result = conn.execute(text(
        f"INSERT INTO call_record (id, {column_sql}, created_at) "
        f"SELECT p.id, {', '.join(f'p.{column}' for column in columns)}, "
        f"COALESCE(datetime(p.task_start_time / 1000.0, 'unixepoch'), CURRENT_TIMESTAMP) "
        f"FROM process_record AS p "
        f"WHERE p.id IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM call_record AS c WHERE c.id = p.id)"
    ))
    return max(result.rowcount or 0, 0)


def _copy_context_log_rows(conn) -> int:
    """Copy generic transcript data while leaving collection-specific columns behind."""
    source_columns = _sqlite_table_columns(conn, "context_log")
    if not {"uuid", "context"}.issubset(source_columns):
        return 0

    result = conn.execute(text(
        "INSERT INTO call_transcript "
        "(id, call_uuid, language, messages_json, created_at) "
        "SELECT c.id, c.uuid, '', c.context, "
        "COALESCE(datetime(c.ctime1 / 1000.0, 'unixepoch'), CURRENT_TIMESTAMP) "
        "FROM context_log AS c "
        "WHERE c.id IS NOT NULL AND c.uuid IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM call_transcript AS t WHERE t.id = c.id)"
    ))
    return max(result.rowcount or 0, 0)


def _init_sqlite_schema():
    with engine.begin() as conn:
        for ddl in _SQLITE_DDL:
            conn.execute(text(ddl))
        copied_records = _copy_process_record_rows(conn)
        copied_transcripts = _copy_context_log_rows(conn)
        if _asr_shadow_compare_enabled():
            conn.execute(text(_ASR_SHADOW_COMPARE_DDL))
    if copied_records or copied_transcripts:
        LOGGER.info(
            "SQLite legacy data copied: call_record=%d, call_transcript=%d",
            copied_records,
            copied_transcripts,
        )
    LOGGER.info("SQLite storage initialized: %s", DATABASE_URL)


def _asr_shadow_compare_enabled() -> bool:
    return os.getenv("ENABLE_ASR_SHADOW_COMPARE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


if IS_SQLITE:
    try:
        _init_sqlite_schema()
    except Exception as exc:
        LOGGER.warning("SQLite schema init failed (storage writes will fail): %s", exc)
else:
    LOGGER.info("Using external database: %s", DATABASE_URL.split("@")[-1])


def _is_disconnect_error(exc: OperationalError) -> bool:
    original = getattr(exc, "orig", None)
    code = None
    if getattr(original, "args", None):
        code = original.args[0]
    return code in {2006, 2013, 2014, 2055}


class DBClient:
    """统一的存储客户端。接口与历史 MysqlClient 完全一致。"""

    @classmethod
    def execute(cls, sql, *params):
        try:
            with Session() as session:
                session.execute(sql, *params)
                session.commit()
        except OperationalError as exc:
            if not _is_disconnect_error(exc):
                raise
            LOGGER.warning(
                "Database connection dropped during execute; disposing pool and retrying once: %s",
                exc,
            )
            engine.dispose()
            with Session() as session:
                session.execute(sql, *params)
                session.commit()

    @classmethod
    def get_many(cls, sql, *params):
        with Session() as session:
            result = session.execute(sql, *params)
            columns = result.keys()
            values = result.fetchall()
            return [dict(zip(columns, value)) for value in values]

    @classmethod
    def get_one(cls, sql, *params):
        with Session() as session:
            result = session.execute(sql, *params)
            value = result.fetchone()
            if value:
                columns = result.keys()
                return dict(zip(columns, value))
            return value


# 历史调用方使用的名字，保留为兼容别名。
MysqlClient = DBClient


def connection_and_select():
    """最简单的连通性自检。"""
    result_one = DBClient.get_one("SELECT 1 AS alive")
    assert result_one is not None, "数据库连接失败"
    assert result_one.get("alive") == 1, "SELECT 1 返回值异常"
    print("✓ 数据库连接正常")


if __name__ == "__main__":
    connection_and_select()
    print(f"\n存储后端: {DATABASE_URL}")
    print("所有基础测试通过！")
