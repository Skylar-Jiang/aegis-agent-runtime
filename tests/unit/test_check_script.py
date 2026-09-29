from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "repository_check", ROOT / "scripts" / "check.py"
)
assert SPEC is not None and SPEC.loader is not None
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def production_function(name: str) -> Any:
    assert hasattr(check, name), f"scripts.check must define {name}"
    return getattr(check, name)


def test_node_metadata_is_pinned_to_node_24() -> None:
    package = json.loads(
        (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")
    )

    assert package["engines"]["node"] == ">=24.14.0 <25"
    assert package["packageManager"] == "pnpm@10.12.4"
    assert (ROOT / ".nvmrc").read_text(encoding="utf-8").strip() == "24.14.0"
    assert (ROOT / ".npmrc").read_text(encoding="utf-8").strip() == "engine-strict=true"


def test_pnpm_version_docs_require_frontend_working_directory() -> None:
    for path in (
        ROOT / "README.md",
        ROOT / "docs" / "02-tech-stack.md",
        ROOT / "docs" / "11-development-guide.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert "frontend" in text
        assert "corepack pnpm --version" in text


def test_authoritative_project_files_have_no_stale_node_22_requirement() -> None:
    authoritative_files = [
        ROOT / "README.md",
        ROOT / ".nvmrc",
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / "frontend" / "package.json",
        ROOT / "docs" / "02-tech-stack.md",
        ROOT / "docs" / "11-development-guide.md",
        ROOT / "docs" / "SHARED-BASELINE-REPORT.md",
    ]

    for path in authoritative_files:
        text = path.read_text(encoding="utf-8").lower()
        assert "node 22" not in text, path
        assert "node.js 22" not in text, path
        assert "22.12" not in text, path
        assert "<23" not in text, path


@pytest.mark.parametrize("version", ["v24.14.0", "v24.99.1"])
def test_node_version_accepts_supported_node_24(version: str) -> None:
    validate_node_version = production_function("validate_node_version")

    validate_node_version(version)


@pytest.mark.parametrize("version", ["v24.13.9", "v25.0.0", "not-a-version"])
def test_node_version_rejects_unsupported_versions(version: str) -> None:
    validate_node_version = production_function("validate_node_version")

    with pytest.raises(ValueError, match="Node.js 24.14.0"):
        validate_node_version(version)


def test_package_manager_version_must_match_expected_pnpm(tmp_path: Path) -> None:
    validate_package_manager = production_function("validate_package_manager")
    package_json = tmp_path / "package.json"
    package_json.write_text('{"packageManager": "pnpm@10.12.3"}', encoding="utf-8")

    with pytest.raises(ValueError, match="pnpm@10.12.4"):
        validate_package_manager(package_json)


@pytest.mark.parametrize("suffix", [".cmd", ".bat"])
def test_windows_batch_executable_uses_comspec(
    monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    subprocess_command = production_function("subprocess_command")
    executable = rf"C:\\tools\\corepack{suffix}"
    monkeypatch.setenv("COMSPEC", r"C:\\Windows\\System32\\cmd.exe")

    command = subprocess_command(executable, ["pnpm", "--version"], windows=True)

    assert command == (
        rf'"C:\\Windows\\System32\\cmd.exe" /d /s /c '
        rf'""{executable}" "pnpm" "--version""'
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe")
def test_windows_batch_command_executes_quoted_spaces(tmp_path: Path) -> None:
    subprocess_command = production_function("subprocess_command")
    batch = tmp_path / "echo safe.cmd"
    batch.write_text("@echo off\r\necho ARG=%~1\r\n", encoding="utf-8")

    command = subprocess_command(str(batch), ["value safe"], windows=True)
    completed = subprocess.run(command, shell=False, capture_output=True, text=True, check=False)

    assert completed.returncode == 0
    assert completed.stdout.strip() == "ARG=value safe"


@pytest.mark.parametrize(
    "unsafe",
    ['bad"quote', "bad%PATH%", "bad!value", "bad&value", "bad|value", "bad\nline"],
)
def test_windows_batch_command_rejects_unsafe_tokens(
    monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    subprocess_command = production_function("subprocess_command")
    monkeypatch.setenv("COMSPEC", r"C:\\Windows\\System32\\cmd.exe")

    with pytest.raises(ValueError, match="Unsafe Windows batch command token"):
        subprocess_command(r"C:\\tools\\corepack.cmd", [unsafe], windows=True)


def test_non_windows_executable_runs_as_argument_list() -> None:
    subprocess_command = production_function("subprocess_command")

    assert subprocess_command(
        "/usr/bin/corepack", ["pnpm", "--version"], windows=False
    ) == [
        "/usr/bin/corepack",
        "pnpm",
        "--version",
    ]


def test_environment_check_resolves_tools_and_checks_corepack_pnpm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    check_environment = production_function("check_environment")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        '{"packageManager": "pnpm@10.12.4"}', encoding="utf-8"
    )
    resolved: list[str] = []

    def fake_which(name: str) -> str | None:
        resolved.append(name)
        return {"node": "/tools/node", "corepack": "/tools/corepack"}.get(name)

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        assert kwargs["shell"] is False
        output = "v24.14.0\n" if command[0].endswith("node") else "10.12.4\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(check.shutil, "which", fake_which)
    monkeypatch.setattr(check.subprocess, "run", fake_run)

    check_environment(tmp_path)

    assert resolved == ["node", "corepack"]


def test_environment_check_reports_missing_node_with_fix_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    check_environment = production_function("check_environment")
    monkeypatch.setattr(check.shutil, "which", lambda _name: None)

    with pytest.raises(ValueError) as error:
        check_environment(tmp_path)

    message = str(error.value)
    assert "Required: >=24.14.0 <25" in message
    assert "Current: not found" in message
    assert "Fix: nvm install 24.14.0" in message


def test_environment_check_rejects_wrong_corepack_pnpm_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    check_environment = production_function("check_environment")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        '{"packageManager": "pnpm@10.12.4"}', encoding="utf-8"
    )
    monkeypatch.setattr(check.shutil, "which", lambda name: f"/tools/{name}")

    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        output = "v24.14.0\n" if command[0].endswith("node") else "10.12.3\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(check.subprocess, "run", fake_run)

    with pytest.raises(ValueError) as error:
        check_environment(tmp_path)

    message = str(error.value)
    assert "Required: pnpm 10.12.4" in message
    assert "Current: 10.12.3" in message
    assert "Fix: corepack prepare pnpm@10.12.4 --activate" in message


def test_environment_check_reports_corepack_failure_with_fix_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    check_environment = production_function("check_environment")
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        '{"packageManager": "pnpm@10.12.4"}', encoding="utf-8"
    )
    monkeypatch.setattr(check.shutil, "which", lambda name: f"/tools/{name}")

    def fake_run(command: list[str], **_kwargs: Any) -> SimpleNamespace:
        if command[0].endswith("node"):
            return SimpleNamespace(returncode=0, stdout="v24.14.0\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="corepack unavailable")

    monkeypatch.setattr(check.subprocess, "run", fake_run)

    with pytest.raises(ValueError) as error:
        check_environment(tmp_path)

    message = str(error.value)
    assert "Required: pnpm 10.12.4" in message
    assert "Current: corepack unavailable" in message
    assert "Fix: corepack prepare pnpm@10.12.4 --activate" in message


def test_main_reports_os_error_from_check_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(check, "check_environment", lambda: None)
    monkeypatch.setattr(
        check, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied"))
    )

    assert check.main() == 1
    assert "denied" in capsys.readouterr().err


def test_use_node_preserves_nvm_list_failure_diagnostics() -> None:
    script = (ROOT / "scripts" / "use-node.ps1").read_text(encoding="utf-8")
    list_call = script.index("nvm list")
    exit_check = script.index("$LASTEXITCODE", list_call)
    installed_check = script.index("$installed -notmatch", list_call)

    assert list_call < exit_check < installed_check
