"""Start the local Core workbench with real SM2, without requiring an LLM.

Run from the repository root:
    uv run --project backend --no-editable python scripts/start_core.py

Development keys and all side effects stay in .runtime/core-demo/. Existing keys
are reused, never silently rotated. This is a local, single-operator experiment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from ra_agent.crypto import OpenSSLSignatureProvider, generate_sm2_key


def prepare(root: Path, *, runtime: Path | None = None) -> dict[str, str]:
    runtime = runtime or root / ".runtime" / "core-demo"
    keys = runtime / "keys"
    keys.mkdir(parents=True, exist_ok=True)
    private = keys / "local-audit.pem"
    public = keys / "trusted-public-keys.json"
    key_id = "local-core-audit-v1"
    if private.exists() != public.exists():
        raise RuntimeError(
            "Incomplete local key pair: restore it before starting; no key overwritten"
        )
    if not private.exists():
        public_pem = generate_sm2_key(private)
        with public.open("x", encoding="utf-8") as stream:
            json.dump({key_id: public_pem}, stream, ensure_ascii=False, indent=2)
    trusted = json.loads(public.read_text(encoding="utf-8"))
    # Validate the actual key pair before opening the HTTP port.
    provider = OpenSSLSignatureProvider(trusted, private_keys={key_id: private})
    probe = b"Aegis Core local startup key check v1"
    signature = provider.sign_sm2(probe, key_id=key_id)
    if not provider.verify_sm2(probe, signature, key_id=key_id):
        raise RuntimeError("Local SM2 key pair does not match")
    workspace = runtime / "workspace"
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    return {
        "PYTHONUTF8": "1",
        "RUNTIME_MODE": "live-agent",
        "CORE_CRYPTO_MODE": "sm2",
        "CORE_SM2_KEY_ID": key_id,
        "CORE_SM2_PUBLIC_KEYS_PATH": str(public),
        "CORE_SM2_PRIVATE_KEY_PATH": str(private),
        "CORE_EVENT_LOG_PATH": str(runtime / "events.jsonl"),
        "CORE_STATE_PATH": str(runtime / "state.sqlite3"),
        "CORE_EVIDENCE_ROOT": str(runtime / "evidence"),
        "CORE_AUDIT_CHECKPOINT_ROOT": str(runtime / "trusted-checkpoints"),
        "CORE_MEMORY_PATH": str(runtime / "memory.json"),
        "CORE_OUTBOX_PATH": str(runtime / "outbox.jsonl"),
        "DATABASE_URL": f"sqlite+aiosqlite:///{(runtime / 'runtime.db').as_posix()}",
        "WORKSPACE_ROOT": str(workspace),
        "PENDING_ROOT": str(runtime / "pending"),
        "CHECKPOINT_ROOT": str(runtime / "checkpoints"),
        "QUARANTINE_ROOT": str(runtime / "quarantine"),
        "SECURITY_CONFIG_DIR": str(root / "configs"),
        "ENABLE_DEMO_FIXTURES": "false",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, **prepare(root)}
    print(
        "Real SM2/SM3 ready. Local development key; no LLM required for Core.",
        flush=True,
    )
    print(f"Workspace: {env['WORKSPACE_ROOT']}", flush=True)
    if args.prepare_only:
        return 0
    print(f"Core health: http://127.0.0.1:{args.port}/api/v1/health", flush=True)
    print("Frontend (second terminal): cd frontend; corepack pnpm dev", flush=True)
    try:
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ra_agent.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            cwd=root,
            env=env,
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
