from enum import StrEnum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreCryptoMode(StrEnum):
    FAKE = "fake"
    SM2 = "sm2"


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
    max_backup_bytes: int | None = Field(default=None, gt=0)
    max_write_bytes: int = 1024 * 1024
    max_list_entries: int = 1000
    max_agent_turns: int = 8
    conversation_recent_messages: int = Field(default=12, gt=0)
    conversation_summary_characters: int = Field(default=4000, gt=0)
    enable_demo_fixtures: bool = False
    intent_baseline_enabled: bool = True
    intent_detection_timeout_seconds: float = Field(default=1.0, gt=0)

    # Core v1 cryptographic evidence stack. Paths are deployment configuration,
    # never accepted from tool calls or Agent-generated payloads.
    core_crypto_mode: CoreCryptoMode = CoreCryptoMode.FAKE
    core_sm2_key_id: str = ""
    core_sm2_public_keys_path: Path | None = None
    core_sm2_private_key_path: Path | None = None
    core_event_log_path: Path = Path(".runtime/core/events.jsonl")
    core_state_path: Path | None = None
    core_evidence_root: Path = Path(".runtime/core/evidence")
    core_audit_checkpoint_root: Path = Path(".runtime/core/trusted-checkpoints")
    core_memory_path: Path = Path(".runtime/core/memory.json")
    core_outbox_path: Path = Path(".runtime/core/outbox.jsonl")
