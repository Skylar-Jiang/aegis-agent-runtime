"""Run reproducible P2 PR1 checks and save their actual output under .runtime."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from check import subprocess_command

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        print("npm is required; install the repository's Node 24.x prerequisite")
        return 2
    python = sys.executable
    commands = [
        (
            "schemas",
            [python, "scripts/export_core_event_contracts.py", "--check"],
            ROOT,
        ),
        (
            "ruff",
            [
                python,
                "-m",
                "ruff",
                "check",
                "backend/src",
                "tests",
                "scripts/export_core_event_contracts.py",
                "scripts/verify_core_p2.py",
            ],
            ROOT,
        ),
        (
            "pyright",
            [python, "-m", "pyright", "--project", "backend/pyproject.toml"],
            ROOT,
        ),
        (
            "backend",
            [
                python,
                "-m",
                "pytest",
                "tests/unit/events",
                "tests/contract/test_core_event_artifacts.py",
                "tests/contract/test_core_v1_contracts.py",
                "tests/unit/core/test_core_tool_gateway.py",
                "tests/integration/test_core_v1_api.py",
                "tests/unit/test_audit.py",
                "tests/unit/database/test_persistent_audit.py",
                "-q",
            ],
            ROOT,
        ),
        *[
            (name, [npm, "run", *args], ROOT / "frontend")
            for name, args in (
                ("frontend-types", ["typecheck"]),
                ("frontend-lint", ["lint"]),
                ("frontend-tests", ["test", "--", "--run"]),
                ("frontend-build", ["build"]),
            )
        ],
    ]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(ROOT / "backend/src"))
    results = []
    for name, command, cwd in commands:
        started = time.monotonic()
        print(f"Running {name}...", flush=True)
        completed = subprocess.run(
            subprocess_command(command[0], command[1:]),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        print(completed.stdout, end="", flush=True)
        print(completed.stderr, end="", file=sys.stderr, flush=True)
        results.append(
            {
                "name": name,
                "command": command,
                "cwd": str(cwd),
                "returncode": completed.returncode,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    passed = all(item["returncode"] == 0 for item in results)
    report = ROOT / ".runtime/p2-validation.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "recorded_at": datetime.now(UTC).isoformat(),
                "checks": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"P2 checks {'PASS' if passed else 'FAIL'}: {report}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
