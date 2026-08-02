from enum import StrEnum
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeMode(StrEnum):
    OFFLINE = "offline"
    RULES_ONLY = "rules-only"
    LIVE_AGENT = "live-agent"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    runtime_mode: RuntimeMode = RuntimeMode.OFFLINE
    database_url: str = "sqlite+aiosqlite:///./.runtime/ra_agent.db"
    security_config_dir: Path = Path("configs")
    llm_base_url: str = ""
    llm_api_key: str = ""
    planner_model: str = ""
    checker_model: str = ""
    workspace_root: Path = Path(".runtime/workspace")
    pending_root: Path = Path(".runtime/pending")
    checkpoint_root: Path = Path(".runtime/checkpoints")
    quarantine_root: Path = Path(".runtime/quarantine")
    max_path_length: int = 4096
    max_read_bytes: int = 1024 * 1024
    max_write_bytes: int = 1024 * 1024
    max_list_entries: int = 1000
    max_agent_turns: int = 8
    enable_demo_fixtures: bool = False
