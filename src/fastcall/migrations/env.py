import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def database_url():
    for name in ("FASTCALL_DATABASE_URL", "DATABASE_URL"):
        url = os.getenv(name, "").strip()
        if url:
            break
    if not url.startswith("mysql+pymysql://"):
        raise RuntimeError("Fast-Call migrations require a MySQL PyMySQL URL")
    return url


def run_migrations_offline():
    context.configure(
        url=database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="robot_config_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table="robot_config_alembic_version",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
