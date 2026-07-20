"""Alembic startup helpers for the persistent Runtime modes."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_database(database_url: str) -> None:
    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    migration_directory = backend_root / "src/ra_agent/database/migrations"
    config.set_main_option("script_location", str(migration_directory))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
