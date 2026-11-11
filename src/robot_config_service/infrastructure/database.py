from dataclasses import dataclass

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker


REQUIRED_TABLES = {
    "robot_app",
    "robot_app_version",
    "robot_release",
    "robot_config_audit_log",
}
ALEMBIC_TABLE = "robot_config_alembic_version"
EXPECTED_REVISION = "0001"


@dataclass
class Database:
    engine: object
    session_factory: object

    @classmethod
    def create(cls, database_url: str):
        engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
        return cls(engine=engine, session_factory=sessionmaker(engine, expire_on_commit=False))

    def verify(self) -> None:
        if self.engine.dialect.name != "mysql":
            raise RuntimeError("Robot Config Service only supports MySQL")
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            table_names = set(inspect(connection).get_table_names())
            missing = (REQUIRED_TABLES | {ALEMBIC_TABLE}) - table_names
            if missing:
                raise RuntimeError(f"Robot Config Service schema is missing: {sorted(missing)}")
            revision = connection.execute(
                text(f"SELECT version_num FROM {ALEMBIC_TABLE}")
            ).scalar()
            if revision != EXPECTED_REVISION:
                raise RuntimeError(
                    f"Robot Config Service schema revision must be {EXPECTED_REVISION}"
                )

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def close(self) -> None:
        self.engine.dispose()
