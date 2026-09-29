"""Alembic startup helpers for the persistent Runtime modes."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade_database(database_url: str) -> None:
    # Resolve packaged migration resources from the module itself. Source-tree
    # parent arithmetic breaks after a non-editable wheel install.
    migration_directory = Path(__file__).resolve().parent / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migration_directory))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
