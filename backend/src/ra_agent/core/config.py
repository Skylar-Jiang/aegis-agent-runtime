from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./ra_agent.db"
    llm_base_url: str = ""
    llm_api_key: str = ""
    planner_model: str = ""
    checker_model: str = ""
    workspace_root: Path = Path(".runtime/workspace")
    pending_root: Path = Path(".runtime/pending")
    checkpoint_root: Path = Path(".runtime/checkpoints")
    quarantine_root: Path = Path(".runtime/quarantine")
