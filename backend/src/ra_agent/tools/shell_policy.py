from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from .path_resolver import PathResolutionError, SafePathResolver


class RestrictedShellPolicyError(ValueError):
    """Base error raised when a process request violates the restricted policy."""


class ShellCommandSyntaxError(RestrictedShellPolicyError):
    """Raised when the command string cannot be safely tokenized."""


class ExecutableNotAllowedError(RestrictedShellPolicyError):
    """Raised when the requested executable is not explicitly allowlisted."""


class ExecutableResolutionError(RestrictedShellPolicyError):
    """Raised when an allowlisted executable cannot be resolved under trusted roots."""


class ShellArgumentError(RestrictedShellPolicyError):
    """Raised when executable-specific arguments violate the allowlist."""


class ShellWorkingDirectoryError(RestrictedShellPolicyError):
    """Raised when cwd is outside the trusted workspace."""


_EXECUTABLE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_FORBIDDEN_EXECUTABLES = frozenset(
    {
        "bash",
        "cmd",
        "cmd.exe",
        "curl",
        "deno",
        "fish",
        "ftp",
        "nc",
        "ncat",
        "netcat",
        "node",
        "node.exe",
        "perl",
        "php",
        "powershell",
        "powershell.exe",
        "pwsh",
        "python",
        "python.exe",
        "python3",
        "pythonw",
        "ruby",
        "scp",
        "sftp",
        "sh",
        "ssh",
        "telnet",
        "wget",
        "zsh",
    }
)
_FORBIDDEN_COMMAND_FRAGMENTS = (
    "&&",
    "||",
    "|",
    ";",
    "`",
    "$(",
    "${",
    ">",
    "<",
    "\r",
    "\n",
    "\x00",
)
_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
)


@dataclass(frozen=True, slots=True)
class ExecutableProfile:
    """Trusted resolution and argument policy for one logical executable."""

    name: str
    candidates: tuple[str, ...]
    validator: str


@dataclass(frozen=True, slots=True)
class ValidatedProcessRequest:
    """Fully validated argv and process context safe for create_subprocess_exec."""

    executable_name: str
    executable_path: Path
    arguments: tuple[str, ...]
    cwd: Path
    cwd_relative: str
    environment: Mapping[str, str]
    timeout_seconds: float


class RestrictedShellPolicy:
    """Parse one frozen command string into a strict, allowlisted argv request."""

    def __init__(
        self,
        resolver: SafePathResolver,
        profiles: Mapping[str, ExecutableProfile],
        *,
        trusted_executable_roots: Sequence[Path],
        default_timeout_seconds: float = 8.0,
        max_timeout_seconds: float = 10.0,
        max_command_length: int = 4096,
        max_arguments: int = 32,
        max_argument_length: int = 1024,
        environment_source: Mapping[str, str] | None = None,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")
        if max_timeout_seconds < default_timeout_seconds:
            raise ValueError("max_timeout_seconds must not be smaller than the default")
        if max_command_length <= 0 or max_arguments <= 0 or max_argument_length <= 0:
            raise ValueError("command and argument limits must be positive")

        roots: list[Path] = []
        for raw_root in trusted_executable_roots:
            resolved = raw_root.resolve(strict=True)
            if not resolved.is_dir():
                raise ExecutableResolutionError("trusted executable root must be a directory")
            if self._is_within(resolved, resolver.workspace_root):
                raise ExecutableResolutionError(
                    "trusted executable root must not be inside the workspace"
                )
            roots.append(resolved)
        if not roots:
            raise ExecutableResolutionError("at least one trusted executable root is required")

        normalized_profiles: dict[str, ExecutableProfile] = {}
        for name, profile in profiles.items():
            normalized_name = self._normalize_executable_name(name)
            if normalized_name in _FORBIDDEN_EXECUTABLES:
                raise ExecutableNotAllowedError(
                    f"forbidden executable cannot be allowlisted: {normalized_name}"
                )
            if profile.name.casefold() != normalized_name:
                raise ValueError("profile mapping key must match profile.name")
            if profile.validator not in {"ruff", "pyright", "test"}:
                raise ValueError(f"unsupported argument validator: {profile.validator}")
            if not profile.candidates:
                raise ValueError(f"profile {profile.name!r} requires candidate paths")
            for candidate in profile.candidates:
                self._validate_candidate_path(candidate)
            normalized_profiles[normalized_name] = profile

        self._resolver = resolver
        self._profiles = normalized_profiles
        self._trusted_roots = tuple(roots)
        self._default_timeout_seconds = default_timeout_seconds
        self._max_timeout_seconds = max_timeout_seconds
        self._max_command_length = max_command_length
        self._max_arguments = max_arguments
        self._max_argument_length = max_argument_length
        source_environment = os.environ if environment_source is None else environment_source
        self._environment_source = dict(source_environment)

    @classmethod
    def from_yaml(
        cls,
        config_path: Path,
        resolver: SafePathResolver,
        *,
        trusted_executable_roots: Sequence[Path],
        environment_source: Mapping[str, str] | None = None,
    ) -> RestrictedShellPolicy:
        """Load the internal restricted_shell section without changing public Contracts."""

        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        if not isinstance(raw, dict):
            raise RestrictedShellPolicyError("tool policy document must be a mapping")
        section = raw.get("restricted_shell")
        if not isinstance(section, dict):
            raise RestrictedShellPolicyError("restricted_shell configuration is missing")
        if section.get("enabled") is not True:
            raise RestrictedShellPolicyError("restricted_shell is not enabled")
        executables = section.get("executables")
        if not isinstance(executables, dict) or not executables:
            raise RestrictedShellPolicyError("restricted_shell executables must be configured")

        profiles: dict[str, ExecutableProfile] = {}
        for name, value in executables.items():
            if not isinstance(name, str) or not isinstance(value, dict):
                raise RestrictedShellPolicyError("invalid restricted_shell executable profile")
            candidates = cls._string_tuple(value.get("candidates"), f"{name}.candidates")
            validator = value.get("validator")
            if not isinstance(validator, str) or not validator:
                raise RestrictedShellPolicyError(f"{name}.validator must be a string")
            if validator == "test":
                raise RestrictedShellPolicyError(
                    "the test argument validator is not allowed in production configuration"
                )
            profiles[name.casefold()] = ExecutableProfile(
                name=name.casefold(),
                candidates=candidates,
                validator=validator,
            )

        return cls(
            resolver,
            profiles,
            trusted_executable_roots=trusted_executable_roots,
            default_timeout_seconds=cls._positive_number(
                section.get("default_timeout_seconds"),
                "default_timeout_seconds",
            ),
            max_timeout_seconds=cls._positive_number(
                section.get("max_timeout_seconds"),
                "max_timeout_seconds",
            ),
            max_command_length=cls._positive_int(
                section.get("max_command_length"),
                "max_command_length",
            ),
            max_arguments=cls._positive_int(
                section.get("max_arguments"),
                "max_arguments",
            ),
            max_argument_length=cls._positive_int(
                section.get("max_argument_length"),
                "max_argument_length",
            ),
            environment_source=environment_source,
        )

    def validate_request(self, arguments: Mapping[str, Any]) -> ValidatedProcessRequest:
        """Validate the current frozen run_shell arguments: command plus optional cwd."""

        unexpected = set(arguments) - {"command", "cwd"}
        if unexpected:
            raise ShellArgumentError(
                "run_shell contains unsupported arguments: " + ", ".join(sorted(unexpected))
            )

        command = arguments.get("command")
        if not isinstance(command, str):
            raise ShellCommandSyntaxError("run_shell argument 'command' must be a string")
        command = command.strip()
        if not command:
            raise ShellCommandSyntaxError("run_shell command must not be empty")
        if len(command) > self._max_command_length:
            raise ShellCommandSyntaxError("run_shell command exceeds configured length")
        if any(fragment in command for fragment in _FORBIDDEN_COMMAND_FRAGMENTS):
            raise ShellCommandSyntaxError("run_shell command contains forbidden shell syntax")

        try:
            tokens = shlex.split(command, posix=True)
        except ValueError as error:
            raise ShellCommandSyntaxError("run_shell command has invalid quoting") from error
        if not tokens:
            raise ShellCommandSyntaxError("run_shell command produced no argv tokens")
        if len(tokens) - 1 > self._max_arguments:
            raise ShellArgumentError("run_shell contains too many arguments")
        for token in tokens:
            self._validate_token(token)

        executable_name = self._normalize_executable_name(tokens[0])
        if executable_name in _FORBIDDEN_EXECUTABLES:
            raise ExecutableNotAllowedError(f"forbidden executable: {executable_name}")
        try:
            profile = self._profiles[executable_name]
        except KeyError as error:
            raise ExecutableNotAllowedError(
                f"executable is not allowlisted: {executable_name}"
            ) from error

        cwd_raw = arguments.get("cwd", ".")
        if not isinstance(cwd_raw, str):
            raise ShellWorkingDirectoryError("run_shell cwd must be a string")
        try:
            cwd = self._resolver.resolve_existing_dir(cwd_raw)
        except PathResolutionError as error:
            raise ShellWorkingDirectoryError(str(error)) from error

        normalized_arguments = self._validate_profile_arguments(
            profile,
            tuple(tokens[1:]),
            cwd,
        )
        executable_path = self._resolve_executable(profile)
        environment = self._build_environment(executable_path)

        return ValidatedProcessRequest(
            executable_name=executable_name,
            executable_path=executable_path,
            arguments=normalized_arguments,
            cwd=cwd,
            cwd_relative=self._resolver.to_relative(cwd),
            environment=environment,
            timeout_seconds=min(
                self._default_timeout_seconds,
                self._max_timeout_seconds,
            ),
        )

    def _validate_profile_arguments(
        self,
        profile: ExecutableProfile,
        arguments: tuple[str, ...],
        cwd: Path,
    ) -> tuple[str, ...]:
        if profile.validator == "ruff":
            return self._validate_ruff(arguments, cwd)
        if profile.validator == "pyright":
            return self._validate_pyright(arguments, cwd)
        if profile.validator == "test":
            if not arguments:
                raise ShellArgumentError("test profile requires a workspace script")
            return (self._normalize_existing_file(arguments[0], cwd), *arguments[1:])
        raise ShellArgumentError("unsupported executable validator")

    def _validate_ruff(self, arguments: tuple[str, ...], cwd: Path) -> tuple[str, ...]:
        if not arguments or arguments[0] not in {"check", "format"}:
            raise ShellArgumentError("ruff requires allowlisted subcommand check or format")
        subcommand = arguments[0]
        allowed_flags = {
            "check": {
                "--diff",
                "--no-cache",
                "--quiet",
                "--show-files",
                "--show-settings",
                "--statistics",
                "--verbose",
            },
            "format": {
                "--check",
                "--diff",
                "--no-cache",
                "--quiet",
                "--verbose",
            },
        }[subcommand]
        allowed_prefixes = ("--ignore=", "--output-format=", "--select=")
        result: list[str] = [subcommand]
        saw_read_only_format_flag = False
        for argument in arguments[1:]:
            if argument.startswith("-"):
                if argument not in allowed_flags and not argument.startswith(allowed_prefixes):
                    raise ShellArgumentError(f"ruff flag is not allowlisted: {argument}")
                if argument in {"--check", "--diff"}:
                    saw_read_only_format_flag = True
                result.append(argument)
                continue
            result.append(self._normalize_existing_path(argument, cwd))
        if subcommand == "format" and not saw_read_only_format_flag:
            raise ShellArgumentError("ruff format requires --check or --diff")
        return tuple(result)

    def _validate_pyright(self, arguments: tuple[str, ...], cwd: Path) -> tuple[str, ...]:
        allowed_flags = {"--outputjson", "--stats", "--verbose", "--warnings"}
        result: list[str] = []
        index = 0
        while index < len(arguments):
            argument = arguments[index]
            if argument == "--project":
                index += 1
                if index >= len(arguments):
                    raise ShellArgumentError("pyright --project requires a path")
                result.extend(["--project", self._normalize_existing_file(arguments[index], cwd)])
            elif argument.startswith("--project="):
                value = argument.partition("=")[2]
                result.append(f"--project={self._normalize_existing_file(value, cwd)}")
            elif argument.startswith("-"):
                if argument not in allowed_flags:
                    raise ShellArgumentError(f"pyright flag is not allowlisted: {argument}")
                result.append(argument)
            else:
                result.append(self._normalize_existing_path(argument, cwd))
            index += 1
        return tuple(result)

    def _normalize_existing_path(self, raw_path: str, cwd: Path) -> str:
        relative_to_workspace = self._cwd_relative_input(raw_path, cwd)
        try:
            resolved = self._resolver.resolve_existing_dir(relative_to_workspace)
        except PathResolutionError:
            try:
                resolved = self._resolver.resolve_existing_file(relative_to_workspace)
            except PathResolutionError as error:
                raise ShellArgumentError(
                    f"tool path is not a safe existing path: {raw_path}"
                ) from error
        return os.path.relpath(resolved, cwd).replace("\\", "/")

    def _normalize_existing_file(self, raw_path: str, cwd: Path) -> str:
        relative_to_workspace = self._cwd_relative_input(raw_path, cwd)
        try:
            resolved = self._resolver.resolve_existing_file(relative_to_workspace)
        except PathResolutionError as error:
            raise ShellArgumentError(
                f"tool file is not a safe existing file: {raw_path}"
            ) from error
        return os.path.relpath(resolved, cwd).replace("\\", "/")

    def _cwd_relative_input(self, raw_path: str, cwd: Path) -> str:
        if Path(raw_path).is_absolute() or PureWindowsPath(raw_path).anchor:
            raise ShellArgumentError("absolute tool paths are not allowed")
        if "\\" in raw_path:
            raise ShellArgumentError("tool paths must use portable forward slashes")
        cwd_relative = self._resolver.to_relative(cwd)
        return raw_path if cwd_relative == "." else f"{cwd_relative}/{raw_path}"

    def _resolve_executable(self, profile: ExecutableProfile) -> Path:
        for root in self._trusted_roots:
            for candidate_text in profile.candidates:
                candidate = root / Path(candidate_text)
                try:
                    resolved = candidate.resolve(strict=True)
                except FileNotFoundError:
                    continue
                if not resolved.is_file() or not self._is_within(resolved, root):
                    continue
                if self._is_within(resolved, self._resolver.workspace_root):
                    continue
                return resolved
        raise ExecutableResolutionError(
            f"allowlisted executable {profile.name!r} was not found under trusted roots"
        )

    def _build_environment(self, executable: Path) -> Mapping[str, str]:
        environment = {
            key: value
            for key, value in self._environment_source.items()
            if key.upper() in _SAFE_ENVIRONMENT_KEYS
        }
        environment["PATH"] = str(executable.parent)
        return dict(sorted(environment.items()))

    def _validate_token(self, token: str) -> None:
        if not token or token.isspace():
            raise ShellArgumentError("run_shell argv contains an empty token")
        if len(token) > self._max_argument_length:
            raise ShellArgumentError("run_shell argument exceeds configured length")
        if token.startswith("@"):
            raise ShellArgumentError("response-file arguments are not allowed")
        if any(ord(char) < 32 or ord(char) == 127 for char in token):
            raise ShellArgumentError("run_shell argv contains a control character")

    @staticmethod
    def _normalize_executable_name(value: str) -> str:
        if not isinstance(value, str) or not _EXECUTABLE_NAME.fullmatch(value):
            raise ExecutableNotAllowedError("executable must be a logical allowlist name")
        if "/" in value or "\\" in value or PureWindowsPath(value).anchor:
            raise ExecutableNotAllowedError("executable paths are not accepted from requests")
        return value.casefold()

    @staticmethod
    def _validate_candidate_path(value: str) -> None:
        if not isinstance(value, str) or not value or value.isspace():
            raise ValueError("candidate executable path must be a non-empty string")
        candidate = PureWindowsPath(value)
        if Path(value).is_absolute() or candidate.anchor or ".." in candidate.parts:
            raise ValueError("candidate executable paths must be safe relative paths")

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _string_tuple(value: object, label: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value:
            raise RestrictedShellPolicyError(f"{label} must be a non-empty list")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item:
                raise RestrictedShellPolicyError(f"{label} entries must be strings")
            result.append(item)
        return tuple(result)

    @staticmethod
    def _positive_int(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RestrictedShellPolicyError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _positive_number(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise RestrictedShellPolicyError(f"{label} must be positive")
        return float(value)
