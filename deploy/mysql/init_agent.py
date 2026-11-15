import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pymysql
from pymysql.constants import CLIENT


def main():
    value = os.getenv("AGENT_DATABASE_URL", "").strip()
    parsed = urlsplit(value)
    database = parsed.path.lstrip("/")
    if parsed.scheme != "mysql+pymysql" or not parsed.hostname or not database or "/" in database:
        raise RuntimeError("AGENT_DATABASE_URL must be a mysql+pymysql URL with one database")
    connection = pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=database,
        autocommit=True,
        client_flag=CLIENT.MULTI_STATEMENTS,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s AND table_name IN ('call_record', 'call_transcript', 'call_analysis')",
                (database,),
            )
            tables = {row[0] for row in cursor.fetchall()}
            if len(tables) == 3:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = 'call_record'",
                    (database,),
                )
                columns = {row[0] for row in cursor.fetchall()}
                required = {"call_source", "claim_generation", "technical_config"}
                if required - columns:
                    raise RuntimeError(
                        "Agent schema is not current; apply the existing historical SQL migrations first"
                    )
                print("Agent MySQL schema is already initialized; no DDL executed", flush=True)
                return
            if tables or os.getenv("AGENT_INIT_SCHEMA", "").lower() not in {"1", "true", "yes", "on"}:
                raise RuntimeError(
                    "Agent database is empty or partial; set AGENT_INIT_SCHEMA=true only for a new empty database"
                )
            sql = Path("/app/deploy/mysql/agent_current_schema.sql").read_text()
            cursor.execute(sql)
            while cursor.nextset():
                pass
            print("Agent MySQL schema initialized for the new database", flush=True)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
