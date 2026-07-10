from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path = ROOT) -> None:
    print(f"+ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    run([sys.executable, "-m", "ruff", "check", "backend/src", "tests"])
    run([sys.executable, "-m", "pyright", "--project", "backend/pyproject.toml"])
    run([sys.executable, "-m", "pytest", "tests", "--cov=ra_agent"])
    run(["corepack", "pnpm", "lint"], ROOT / "frontend")
    run(["corepack", "pnpm", "typecheck"], ROOT / "frontend")
    run(["corepack", "pnpm", "exec", "vitest", "run"], ROOT / "frontend")
    run(["corepack", "pnpm", "build"], ROOT / "frontend")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
