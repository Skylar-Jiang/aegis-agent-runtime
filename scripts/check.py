from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_REQUIREMENT = ">=24.14.0 <25"
EXPECTED_PNPM = "10.12.4"


def validate_node_version(version: str) -> None:
    try:
        parsed = tuple(
            int(part) for part in version.strip().removeprefix("v").split(".")
        )
    except ValueError:
        parsed = ()
    if len(parsed) != 3 or not (parsed >= (24, 14, 0) and parsed < (25, 0, 0)):
        raise ValueError(
            "Node.js 24.14.0 requirement not met.\n"
            f"Required: {NODE_REQUIREMENT}\n"
            f"Current: {version.strip() or 'unknown'}\n"
            "Fix: nvm install 24.14.0 && nvm use 24.14.0"
        )


def validate_package_manager(package_json: Path) -> None:
    package = json.loads(package_json.read_text(encoding="utf-8"))
    current = package.get("packageManager", "not set")
    expected = f"pnpm@{EXPECTED_PNPM}"
    if current != expected:
        raise ValueError(
            "Frontend package manager requirement not met.\n"
            f"Required: {expected}\n"
            f"Current: {current}\n"
            f"Fix: set frontend/package.json packageManager to {expected}"
        )


def subprocess_command(
    executable: str, arguments: list[str], *, windows: bool | None = None
) -> str | list[str]:
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        comspec = os.environ.get("COMSPEC")
        if not comspec:
            raise ValueError("COMSPEC is required to run Windows .cmd/.bat executables")
        tokens = [comspec, executable, *arguments]
        unsafe = {'"', "%", "!", "&", "|", "<", ">", "^", "(", ")", "\r", "\n", "\0"}
        if any(character in token for token in tokens for character in unsafe):
            raise ValueError("Unsafe Windows batch command token")
        batch_command = " ".join(f'"{token}"' for token in [executable, *arguments])
        return f'"{comspec}" /d /s /c "{batch_command}"'
    return [executable, *arguments]


def resolve_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise ValueError(f"Required executable not found: {name}")
    return executable


def command_output(executable: str, arguments: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        subprocess_command(executable, arguments),
        cwd=cwd,
        shell=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ValueError(detail)
    return completed.stdout.strip()


def check_environment(root: Path = ROOT) -> None:
    node = shutil.which("node")
    if node is None:
        raise ValueError(
            "Node.js requirement not met.\n"
            f"Required: {NODE_REQUIREMENT}\n"
            "Current: not found\n"
            "Fix: nvm install 24.14.0 && nvm use 24.14.0"
        )
    try:
        node_version = command_output(node, ["--version"], root)
    except ValueError as error:
        raise ValueError(
            "Node.js requirement not met.\n"
            f"Required: {NODE_REQUIREMENT}\n"
            f"Current: {error}\n"
            "Fix: nvm install 24.14.0 && nvm use 24.14.0"
        ) from error
    validate_node_version(node_version)

    validate_package_manager(root / "frontend" / "package.json")

    corepack = shutil.which("corepack")
    if corepack is None:
        raise ValueError(
            "pnpm requirement not met.\n"
            f"Required: pnpm {EXPECTED_PNPM} via corepack\n"
            "Current: corepack not found\n"
            f"Fix: corepack prepare pnpm@{EXPECTED_PNPM} --activate"
        )
    try:
        current_pnpm = command_output(
            corepack, ["pnpm", "--version"], root / "frontend"
        )
    except ValueError as error:
        raise ValueError(
            "pnpm requirement not met.\n"
            f"Required: pnpm {EXPECTED_PNPM}\n"
            f"Current: {error}\n"
            f"Fix: corepack prepare pnpm@{EXPECTED_PNPM} --activate"
        ) from error
    if current_pnpm != EXPECTED_PNPM:
        raise ValueError(
            "pnpm requirement not met.\n"
            f"Required: pnpm {EXPECTED_PNPM}\n"
            f"Current: {current_pnpm or 'unknown'}\n"
            f"Fix: corepack prepare pnpm@{EXPECTED_PNPM} --activate"
        )


def run(command: list[str], cwd: Path = ROOT) -> int:
    executable = resolve_executable(command[0])
    print(f"+ {' '.join(command)}")
    completed = subprocess.run(
        subprocess_command(executable, command[1:]), cwd=cwd, shell=False
    )
    return completed.returncode


def main() -> int:
    try:
        check_environment()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(error, file=sys.stderr)
        return 1

    checks = [
        ([sys.executable, "-m", "ruff", "check", "backend/src", "tests"], ROOT),
        (
            [sys.executable, "-m", "pyright", "--project", "backend/pyproject.toml"],
            ROOT,
        ),
        ([sys.executable, "-m", "pytest", "tests", "--cov=ra_agent"], ROOT),
        (["corepack", "pnpm", "lint"], ROOT / "frontend"),
        (["corepack", "pnpm", "typecheck"], ROOT / "frontend"),
        (["corepack", "pnpm", "exec", "vitest", "run"], ROOT / "frontend"),
        (["corepack", "pnpm", "build"], ROOT / "frontend"),
    ]
    for command, cwd in checks:
        try:
            returncode = run(command, cwd)
        except (OSError, ValueError) as error:
            print(error, file=sys.stderr)
            return 1
        if returncode != 0:
            return returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
