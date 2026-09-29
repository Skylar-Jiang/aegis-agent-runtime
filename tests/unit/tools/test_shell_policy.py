from __future__ import annotations

import sys
from pathlib import Path

import pytest
from ra_agent.tools.path_resolver import SafePathResolver
from ra_agent.tools.shell_policy import (
    ExecutableNotAllowedError,
    ExecutableProfile,
    RestrictedShellPolicy,
    ShellArgumentError,
    ShellCommandSyntaxError,
    ShellWorkingDirectoryError,
)


def make_resolver(workspace: Path) -> SafePathResolver:
    return SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024 * 1024,
        max_write_bytes=1024 * 1024,
    )


def make_policy(
    workspace: Path,
    trusted_root: Path,
    profiles: dict[str, ExecutableProfile],
) -> RestrictedShellPolicy:
    return RestrictedShellPolicy(
        make_resolver(workspace),
        profiles,
        trusted_executable_roots=(trusted_root,),
        environment_source={
            "LANG": "C.UTF-8",
            "API_KEY": "must-not-leak",
            "TOKEN": "must-not-leak",
            "PATH": str(workspace),
        },
    )


def create_executable(root: Path, name: str) -> Path:
    executable = root / name
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(b"test executable")
    return executable


def test_ruff_read_only_command_is_validated(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    (workspace / "backend").mkdir()
    create_executable(trusted, "ruff-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "ruff": ExecutableProfile(
                name="ruff",
                candidates=("ruff-bin",),
                validator="ruff",
            )
        },
    )

    validated = policy.validate_request(
        {"command": "ruff check --no-cache backend", "cwd": "."}
    )

    assert validated.executable_name == "ruff"
    assert validated.arguments == ("check", "--no-cache", "backend")
    assert validated.cwd == workspace.resolve()
    assert validated.cwd_relative == "."
    assert set(validated.environment) == {"LANG", "PATH"}
    assert str(workspace) not in validated.environment["PATH"]


@pytest.mark.parametrize(
    "command",
    [
        "cmd /c echo test",
        "powershell -Command Get-ChildItem",
        "bash -c pwd",
        "python -c pass",
        "curl https://example.com",
        "ssh example.com",
        "unknown --version",
    ],
)
def test_forbidden_or_unknown_executable_is_rejected(
    tmp_path: Path,
    command: str,
) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    create_executable(trusted, "ruff-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "ruff": ExecutableProfile(
                name="ruff",
                candidates=("ruff-bin",),
                validator="ruff",
            )
        },
    )

    with pytest.raises(ExecutableNotAllowedError):
        policy.validate_request({"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "ruff check . && curl example.com",
        "ruff check . | more",
        "ruff check . > result.txt",
        "ruff check $(whoami)",
        "ruff check `whoami`",
        "ruff check\nbackend",
    ],
)
def test_shell_syntax_is_rejected(tmp_path: Path, command: str) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    create_executable(trusted, "ruff-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "ruff": ExecutableProfile(
                name="ruff",
                candidates=("ruff-bin",),
                validator="ruff",
            )
        },
    )

    with pytest.raises(ShellCommandSyntaxError):
        policy.validate_request({"command": command})


def test_ruff_format_requires_read_only_flag(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    create_executable(trusted, "ruff-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "ruff": ExecutableProfile(
                name="ruff",
                candidates=("ruff-bin",),
                validator="ruff",
            )
        },
    )

    with pytest.raises(ShellArgumentError, match="--check or --diff"):
        policy.validate_request({"command": "ruff format ."})


@pytest.mark.parametrize("argument", ["--fix", "@args.txt", "--config=../evil.toml"])
def test_unsafe_ruff_argument_is_rejected(tmp_path: Path, argument: str) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    create_executable(trusted, "ruff-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "ruff": ExecutableProfile(
                name="ruff",
                candidates=("ruff-bin",),
                validator="ruff",
            )
        },
    )

    with pytest.raises(ShellArgumentError):
        policy.validate_request({"command": f"ruff check {argument}"})


def test_pyright_project_path_must_remain_in_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    (workspace / "pyproject.toml").write_text("[tool.pyright]\n")
    create_executable(trusted, "pyright-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "pyright": ExecutableProfile(
                name="pyright",
                candidates=("pyright-bin",),
                validator="pyright",
            )
        },
    )

    validated = policy.validate_request(
        {"command": "pyright --project pyproject.toml --warnings"}
    )
    assert validated.arguments == ("--project", "pyproject.toml", "--warnings")

    with pytest.raises(ShellArgumentError):
        policy.validate_request({"command": "pyright --project ../outside.json"})


def test_cwd_must_be_existing_workspace_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    create_executable(trusted, "ruff-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "ruff": ExecutableProfile(
                name="ruff",
                candidates=("ruff-bin",),
                validator="ruff",
            )
        },
    )

    with pytest.raises(ShellWorkingDirectoryError):
        policy.validate_request({"command": "ruff check .", "cwd": "../outside"})


def test_request_cannot_supply_executable_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    create_executable(trusted, "ruff-bin")
    policy = make_policy(
        workspace,
        trusted,
        {
            "ruff": ExecutableProfile(
                name="ruff",
                candidates=("ruff-bin",),
                validator="ruff",
            )
        },
    )

    with pytest.raises(ExecutableNotAllowedError):
        policy.validate_request({"command": f"{sys.executable} -V"})


def test_policy_loads_restricted_shell_profiles_from_yaml(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted"
    workspace.mkdir()
    trusted.mkdir()
    (workspace / "backend").mkdir()
    create_executable(trusted, "ruff-bin")
    config = tmp_path / "tool_policies.yaml"
    config.write_text(
        """
restricted_shell:
  enabled: true
  default_timeout_seconds: 8
  max_timeout_seconds: 10
  max_command_length: 4096
  max_arguments: 32
  max_argument_length: 1024
  executables:
    ruff:
      candidates: [ruff-bin]
      validator: ruff
""".strip(),
        encoding="utf-8",
    )

    policy = RestrictedShellPolicy.from_yaml(
        config,
        make_resolver(workspace),
        trusted_executable_roots=(trusted,),
        environment_source={},
    )

    validated = policy.validate_request({"command": "ruff check backend"})
    assert validated.executable_name == "ruff"
