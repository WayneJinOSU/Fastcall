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

LOGGER = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SQLITE_URL = f"sqlite:///{os.path.join(_PROJECT_ROOT, 'data', 'collection_agents.db')}"

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

_SQLITE_DDL = [
    """
    CREATE TABLE IF NOT EXISTS callback_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        callee TEXT,
        business_id TEXT,
        data TEXT,
        ctime1 INTEGER,
        uuid TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS context_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_name TEXT,
        business_id TEXT,
        remaining_amount TEXT,
        context TEXT,
        app_type TEXT,
        country TEXT,
        ctime1 INTEGER,
        uuid TEXT
    )
    """,
    """
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
    """,
    """
    CREATE TABLE IF NOT EXISTS process_record (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_uuid TEXT,
        task_duration_sec REAL,
        task_start_time INTEGER,
        task_end_time INTEGER,
        asr_cost REAL,
        llm_cost REAL,
        tts_cost REAL,
        total_cost REAL,
        business_id TEXT,
        tenant_id TEXT,
        app_type TEXT,
        follower_id TEXT,
        dial_schedule TEXT,
        customer_group TEXT,
        has_sms TEXT,
        has_app_push TEXT,
        work_order_id TEXT,
        outbound_source TEXT,
        platform TEXT,
        is_round_robin TEXT,
        is_human_assist TEXT,
        is_recovered TEXT,
        is_voicemail TEXT,
        collection_result TEXT,
        robot_code TEXT,
        robot_name TEXT,
        org_code TEXT,
        org_name TEXT,
        country TEXT,
        task_type TEXT,
        asr_model_name TEXT,
        asr_param_config TEXT,
        asr_ttfb_list TEXT,
        asr_total_transcribed_chars REAL,
        asr_total_audio_duration REAL,
        asr_reconnect_count INTEGER,
        asr_connection_requests INTEGER,
        llm_model_name TEXT,
        llm_param TEXT,
        llm_prompt TEXT,
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
        tts_connection_requests INTEGER
    )
    """,
]


def _init_sqlite_schema():
    with engine.begin() as conn:
        for ddl in _SQLITE_DDL:
            conn.execute(text(ddl))
    LOGGER.info("SQLite storage initialized: %s", DATABASE_URL)


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
