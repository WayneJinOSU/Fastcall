from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@dataclass
class Database:
    engine: object
    session_factory: object

    @classmethod
    def create(cls, database_url: str):
        engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
        return cls(engine=engine, session_factory=sessionmaker(engine, expire_on_commit=False))

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def close(self) -> None:
        self.engine.dispose()
