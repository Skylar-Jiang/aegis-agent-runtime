"""Semantic effect-target inference used by runtime graph scheduling.

The scheduler deliberately derives these records from trusted built-in request
arguments.  Planner-provided ``effect_targets`` are not used as authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from ra_agent.contracts import ToolCallRequest


class EffectOperation(StrEnum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


class EffectScope(StrEnum):
    EXACT = "exact"
    SUBTREE = "subtree"


@dataclass(frozen=True, slots=True)
class EffectTarget:
    resource_type: str
    canonical_target: str
    operation: EffectOperation
    scope: EffectScope

    def render(self) -> str:
        return ":".join(
            (self.resource_type, self.canonical_target, self.operation.value, self.scope.value)
        )


def infer_effect_targets(request: ToolCallRequest) -> tuple[EffectTarget, ...]:
    """Return trusted semantic targets, or one conservative untrusted target."""

    path_operations = {
        "read_file": (EffectOperation.READ, EffectScope.EXACT),
        "create_file": (EffectOperation.WRITE, EffectScope.EXACT),
        "write_file": (EffectOperation.WRITE, EffectScope.EXACT),
        "delete_file": (EffectOperation.DELETE, EffectScope.EXACT),
        "list_dir": (EffectOperation.READ, EffectScope.SUBTREE),
    }
    path_spec = path_operations.get(request.tool_name)
    if path_spec is not None:
        path = request.arguments.get("path")
        normalized = _safe_relative_path(path, allow_root=request.tool_name == "list_dir")
        if normalized is not None:
            return (EffectTarget("file", normalized, *path_spec),)
        return (_untrusted_target(),)

    memory_operations = {
        "memory_read": EffectOperation.READ,
        "memory_write": EffectOperation.WRITE,
    }
    memory_operation = memory_operations.get(request.tool_name)
    if memory_operation is not None:
        key = request.arguments.get("key")
        if isinstance(key, str) and key and key == key.strip() and not _has_control(key):
            return (EffectTarget("memory", key, memory_operation, EffectScope.EXACT),)
        return (_untrusted_target(),)

    if request.tool_name == "download_url":
        url = request.arguments.get("url")
        normalized_url = _normalized_http_url(url)
        if normalized_url is not None:
            return (
                EffectTarget("download", normalized_url, EffectOperation.WRITE, EffectScope.EXACT),
            )
    return (_untrusted_target(),)


def conflict_reason(first: EffectTarget, second: EffectTarget) -> str | None:
    """Return why two targets must serialize, otherwise ``None``."""

    if first.resource_type != second.resource_type:
        return None
    if first.resource_type == "untrusted" or second.resource_type == "untrusted":
        return "untrusted_side_effect"
    if not _targets_overlap(first, second):
        return None
    if first.operation is EffectOperation.READ and second.operation is EffectOperation.READ:
        return None
    if first.scope is EffectScope.SUBTREE or second.scope is EffectScope.SUBTREE:
        return "subtree_read_write"
    if first.operation is EffectOperation.READ or second.operation is EffectOperation.READ:
        return "read_write_exact"
    return "write_write_exact"


def _targets_overlap(first: EffectTarget, second: EffectTarget) -> bool:
    if first.canonical_target == second.canonical_target:
        return True
    return (
        first.scope is EffectScope.SUBTREE
        and _contains(first.canonical_target, second.canonical_target)
    ) or (
        second.scope is EffectScope.SUBTREE
        and _contains(second.canonical_target, first.canonical_target)
    )


def _contains(parent: str, child: str) -> bool:
    return parent == "." or child.startswith(f"{parent}/")


def _safe_relative_path(value: object, *, allow_root: bool) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or ":" in value
    ):
        return None
    candidate = PurePosixPath(value)
    if any(part == ".." for part in candidate.parts):
        return None
    normalized = candidate.as_posix()
    if normalized == ".":
        return normalized if allow_root else None
    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def _normalized_http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    host = parsed.hostname.casefold()
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, ""))


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 for character in value)


def _untrusted_target() -> EffectTarget:
    return EffectTarget("untrusted", "side_effect", EffectOperation.WRITE, EffectScope.SUBTREE)
