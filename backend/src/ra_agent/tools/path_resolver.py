from __future__ import annotations

import fnmatch
import os
from pathlib import Path, PureWindowsPath

DEFAULT_SENSITIVE_PATTERNS = (
    ".env",
    ".env.*",
    "id_rsa",
    "id_ed25519",
    ".ssh",
    "credentials",
    "*credential*",
    "*token*",
    "*secret*",
    "*.pem",
    "*.key",
)

_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')


class PathResolutionError(ValueError):
    """Base error raised when a tool path cannot be used safely."""


class UnsafePathError(PathResolutionError):
    """Raised for absolute, traversing, escaping, or malformed paths."""


class SensitivePathError(PathResolutionError):
    """Raised when a path matches a configured sensitive pattern."""


class PathTypeError(PathResolutionError):
    """Raised when a path does not have the required file-system type."""


class PathSizeError(PathResolutionError):
    """Raised when file content exceeds a configured byte limit."""


class SafePathResolver:
    """Resolve untrusted tool paths inside one trusted workspace root."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        sensitive_patterns: tuple[str, ...] = DEFAULT_SENSITIVE_PATTERNS,
        max_path_length: int,
        max_read_bytes: int,
        max_write_bytes: int,
    ) -> None:
        if max_path_length <= 0:
            raise ValueError("max_path_length must be positive")

        if max_read_bytes <= 0:
            raise ValueError("max_read_bytes must be positive")

        if max_write_bytes <= 0:
            raise ValueError("max_write_bytes must be positive")

        if not workspace_root.exists():
            raise PathTypeError("workspace root does not exist")

        resolved_root = workspace_root.resolve(strict=True)

        if not resolved_root.is_dir():
            raise PathTypeError("workspace root must be a directory")

        self._workspace_root = resolved_root
        self._sensitive_patterns = tuple(pattern.casefold() for pattern in sensitive_patterns)
        self._max_path_length = max_path_length
        self._max_read_bytes = max_read_bytes
        self._max_write_bytes = max_write_bytes

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    def resolve_existing_file(self, raw_path: str) -> Path:
        """Resolve a readable regular file inside the workspace."""

        relative_path = self._validate_relative_path(raw_path)
        resolved = self._resolve_existing(relative_path)

        if not resolved.is_file():
            raise PathTypeError(f"path is not a regular file: {raw_path}")

        size_bytes = resolved.stat().st_size

        if size_bytes > self._max_read_bytes:
            raise PathSizeError(
                f"file exceeds configured read limit: {size_bytes} > {self._max_read_bytes} bytes"
            )

        return resolved

    def resolve_existing_dir(self, raw_path: str) -> Path:
        """Resolve an existing directory inside the workspace."""

        relative_path = self._validate_relative_path(raw_path)
        resolved = self._resolve_existing(relative_path)

        if not resolved.is_dir():
            raise PathTypeError(f"path is not a directory: {raw_path}")

        return resolved

    def resolve_write_target(self, raw_path: str) -> Path:
        """
        Resolve a target for write_file.

        The parent directory must already exist. This method does not create
        directories and does not modify the workspace.
        """

        relative_path = self._validate_relative_path(raw_path)
        candidate = self._workspace_root / relative_path

        try:
            resolved_parent = candidate.parent.resolve(strict=True)
        except FileNotFoundError as error:
            raise PathTypeError(f"parent directory does not exist: {raw_path}") from error

        self._ensure_within_workspace(resolved_parent)

        if not resolved_parent.is_dir():
            raise PathTypeError(f"parent path is not a directory: {raw_path}")

        target = resolved_parent / candidate.name

        # 第一版不允许通过符号链接修改文件。
        if target.is_symlink():
            raise UnsafePathError(f"symbolic-link write target is not allowed: {raw_path}")

        if target.exists() and not target.is_file():
            raise PathTypeError(f"write target is not a regular file: {raw_path}")

        self._ensure_within_workspace(target)

        return target

    def resolve_delete_target(self, raw_path: str) -> Path:
        """
        Resolve a file that may later be staged for deletion.

        The first version only permits regular files and rejects symbolic
        links and directories.
        """

        relative_path = self._validate_relative_path(raw_path)
        candidate = self._workspace_root / relative_path

        if candidate.is_symlink():
            raise UnsafePathError(f"symbolic-link deletion is not allowed: {raw_path}")

        resolved = self._resolve_existing(relative_path)

        if not resolved.is_file():
            raise PathTypeError(f"delete target is not a regular file: {raw_path}")

        return resolved

    def validate_write_content(self, content: str | bytes) -> int:
        """Validate write payload size and return the UTF-8 byte count."""

        payload = content.encode("utf-8") if isinstance(content, str) else content
        size_bytes = len(payload)

        if size_bytes > self._max_write_bytes:
            raise PathSizeError(
                "content exceeds configured write limit: "
                f"{size_bytes} > {self._max_write_bytes} bytes"
            )

        return size_bytes

    def validate_read_content(self, content: bytes) -> int:
        """Validate content after reading and return its byte count."""
        size_bytes = len(content)
        if size_bytes > self._max_read_bytes:
            raise PathSizeError(
                "file exceeds configured read limit after reading: "
                f"{size_bytes} > {self._max_read_bytes} bytes"
            )

        return size_bytes

    def to_relative(self, path: Path) -> str:
        """Convert a trusted absolute path to a portable workspace-relative path."""

        resolved = path.resolve(strict=False)
        self._ensure_within_workspace(resolved)

        relative = os.path.relpath(resolved, self._workspace_root)
        return Path(relative).as_posix()

    def is_sensitive_path(self, path: Path) -> bool:
        """Return whether a workspace path matches a sensitive pattern."""

        resolved = path.resolve(strict=False)
        self._ensure_within_workspace(resolved)

        relative = Path(
            os.path.relpath(
                resolved,
                self._workspace_root,
            )
        )

        try:
            self._reject_sensitive_path(relative)
        except SensitivePathError:
            return True

        return False

    def _resolve_existing(self, relative_path: Path) -> Path:
        candidate = self._workspace_root / relative_path

        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise PathTypeError(f"path does not exist: {relative_path.as_posix()}") from error

        # resolve() 后再判断，才能发现符号链接逃逸。
        self._ensure_within_workspace(resolved)

        return resolved

    def _validate_relative_path(self, raw_path: str) -> Path:
        if not isinstance(raw_path, str):
            raise UnsafePathError("path must be a string")

        if not raw_path or raw_path.isspace():
            raise UnsafePathError("path must not be empty")

        if len(raw_path) > self._max_path_length:
            raise UnsafePathError("path exceeds configured length limit")

        if "\x00" in raw_path:
            raise UnsafePathError("path contains a null byte")

        windows_path = PureWindowsPath(raw_path)

        # 同时识别当前操作系统绝对路径和 Windows 绝对路径。
        if Path(raw_path).is_absolute() or windows_path.is_absolute() or bool(windows_path.anchor):
            raise UnsafePathError(f"absolute or anchored path is not allowed: {raw_path}")

        parts = windows_path.parts

        # 显式拒绝 ..，即使 resolve 后最终仍落在 workspace 内。
        if any(part == ".." for part in parts):
            raise UnsafePathError(f"path traversal is not allowed: {raw_path}")

        normalized_parts: list[str] = []

        for part in parts:
            if part in {"", "."}:
                continue

            self._validate_component(part)
            normalized_parts.append(part)

        # "." 表示 workspace 根目录，主要供 list_dir 使用。
        if not normalized_parts:
            normalized_parts.append(".")

        normalized = Path(*normalized_parts)

        self._reject_sensitive_path(normalized)

        return normalized

    def _validate_component(self, component: str) -> None:
        if any(ord(char) < 32 for char in component):
            raise UnsafePathError("path contains a control character")

        if any(char in _WINDOWS_INVALID_CHARS for char in component):
            raise UnsafePathError(f"path contains an illegal character: {component}")

        if component.endswith((" ", ".")):
            raise UnsafePathError(f"path component has an unsafe suffix: {component}")

        reserved_stem = component.split(".", maxsplit=1)[0].upper()

        if reserved_stem in _WINDOWS_RESERVED_NAMES:
            raise UnsafePathError(f"reserved Windows path component: {component}")

    def _reject_sensitive_path(self, relative_path: Path) -> None:
        relative_text = relative_path.as_posix().casefold()
        components = tuple(part.casefold() for part in relative_path.parts)

        for pattern in self._sensitive_patterns:
            normalized_pattern = pattern.replace("\\", "/")

            # 包含路径分隔符的规则匹配完整相对路径。
            if "/" in normalized_pattern:
                if fnmatch.fnmatchcase(relative_text, normalized_pattern):
                    raise SensitivePathError(f"sensitive path is not allowed: {relative_text}")

                continue

            # 普通模式匹配任意一级路径组件。
            if any(fnmatch.fnmatchcase(component, normalized_pattern) for component in components):
                raise SensitivePathError(f"sensitive path is not allowed: {relative_text}")

    def _ensure_within_workspace(self, path: Path) -> None:
        """
        Verify containment after path normalization and symlink resolution.

        normcase() makes this comparison case-insensitive on Windows while
        preserving case-sensitive behavior on Linux.
        """

        root_text = os.path.normcase(os.path.abspath(self._workspace_root))
        path_text = os.path.normcase(os.path.abspath(path))

        try:
            common = os.path.commonpath((root_text, path_text))
        except ValueError as error:
            # Windows 不同盘符会触发 ValueError。
            raise UnsafePathError("path is on a different drive than the workspace") from error

        if common != root_text:
            raise UnsafePathError("resolved path escapes the workspace")
