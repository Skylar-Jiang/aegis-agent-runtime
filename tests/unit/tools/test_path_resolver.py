from pathlib import Path

import pytest

from ra_agent.tools.path_resolver import (
    PathSizeError,
    PathTypeError,
    SafePathResolver,
    SensitivePathError,
    UnsafePathError,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()

    (root / "docs").mkdir()
    (root / "README.md").write_text(
        "hello",
        encoding="utf-8",
    )
    (root / "docs" / "report.txt").write_text(
        "report",
        encoding="utf-8",
    )

    return root


@pytest.fixture
def resolver(workspace: Path) -> SafePathResolver:
    return SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=16,
        max_write_bytes=16,
    )


def test_existing_file_accepts_safe_relative_path(
    resolver: SafePathResolver,
    workspace: Path,
) -> None:
    result = resolver.resolve_existing_file("docs/report.txt")

    assert result == (workspace / "docs" / "report.txt").resolve()


def test_existing_dir_accepts_workspace_dot(
    resolver: SafePathResolver,
    workspace: Path,
) -> None:
    assert resolver.resolve_existing_dir(".") == workspace.resolve()


@pytest.mark.parametrize(
    "raw_path",
    [
        r"C:\Users\user\.env",
        "D:/secret.txt",
        "/home/user/file",
        r"\\server\share\file",
        r"\Windows\System32",
    ],
)
def test_absolute_and_anchored_paths_are_rejected(
    resolver: SafePathResolver,
    raw_path: str,
) -> None:
    with pytest.raises(UnsafePathError):
        resolver.resolve_existing_file(raw_path)


@pytest.mark.parametrize(
    "raw_path",
    [
        "../secret.txt",
        "../../.env",
        "docs/../../../Windows/System32",
        r"docs\..\secret.txt",
    ],
)
def test_path_traversal_is_rejected(
    resolver: SafePathResolver,
    raw_path: str,
) -> None:
    with pytest.raises(UnsafePathError):
        resolver.resolve_existing_file(raw_path)


@pytest.mark.parametrize(
    "raw_path",
    [
        ".env",
        ".ENV.production",
        ".ssh/id_rsa",
        "config/api-token.txt",
        "private/client_secret.json",
        "certs/server.pem",
        "certs/server.KEY",
    ],
)
def test_sensitive_paths_are_rejected(
    resolver: SafePathResolver,
    raw_path: str,
) -> None:
    with pytest.raises(SensitivePathError):
        resolver.resolve_write_target(raw_path)


@pytest.mark.parametrize(
    "raw_path",
    [
        "bad?.txt",
        "bad*.txt",
        "NUL.txt",
        "COM1.log",
        "trailing-dot.",
    ],
)
def test_illegal_windows_components_are_rejected(
    resolver: SafePathResolver,
    raw_path: str,
) -> None:
    with pytest.raises(UnsafePathError):
        resolver.resolve_write_target(raw_path)


def test_existing_file_rejects_directory(
    resolver: SafePathResolver,
) -> None:
    with pytest.raises(PathTypeError):
        resolver.resolve_existing_file("docs")


def test_existing_dir_rejects_file(
    resolver: SafePathResolver,
) -> None:
    with pytest.raises(PathTypeError):
        resolver.resolve_existing_dir("README.md")


def test_existing_file_rejects_missing_path(
    resolver: SafePathResolver,
) -> None:
    with pytest.raises(PathTypeError):
        resolver.resolve_existing_file("missing.txt")


def test_write_target_requires_existing_parent(
    resolver: SafePathResolver,
) -> None:
    with pytest.raises(PathTypeError):
        resolver.resolve_write_target("missing/report.txt")


def test_write_target_rejects_directory(
    resolver: SafePathResolver,
) -> None:
    with pytest.raises(PathTypeError):
        resolver.resolve_write_target("docs")


def test_read_limit_is_enforced(
    workspace: Path,
) -> None:
    (workspace / "large.txt").write_text(
        "x" * 17,
        encoding="utf-8",
    )

    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=16,
        max_write_bytes=16,
    )

    with pytest.raises(PathSizeError):
        resolver.resolve_existing_file("large.txt")


def test_write_limit_is_measured_in_utf8_bytes(
    resolver: SafePathResolver,
) -> None:
    assert resolver.validate_write_content("abc") == 3

    # 一个汉字通常占用三个 UTF-8 字节，六个字为十八字节。
    with pytest.raises(PathSizeError):
        resolver.validate_write_content("你" * 6)


def test_to_relative_returns_posix_style_path(
    resolver: SafePathResolver,
    workspace: Path,
) -> None:
    result = resolver.to_relative(workspace / "docs" / "report.txt")

    assert result == "docs/report.txt"


def test_read_content_limit_is_enforced_after_reading(
    resolver: SafePathResolver,
) -> None:
    assert resolver.validate_read_content(b"abc") == 3

    with pytest.raises(PathSizeError):
        resolver.validate_read_content(b"x" * 17)


def test_symbolic_link_escape_is_rejected(
    resolver: SafePathResolver,
    workspace: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    (outside / "public.txt").write_text(
        "outside",
        encoding="utf-8",
    )

    link = workspace / "outside-link"

    try:
        link.symlink_to(
            outside,
            target_is_directory=True,
        )
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

    with pytest.raises(UnsafePathError):
        resolver.resolve_existing_file("outside-link/public.txt")


def test_delete_rejects_symbolic_link(
    resolver: SafePathResolver,
    workspace: Path,
) -> None:
    target = workspace / "README.md"
    link = workspace / "readme-link.txt"

    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

    with pytest.raises(UnsafePathError):
        resolver.resolve_delete_target("readme-link.txt")

@pytest.mark.parametrize(
    ("field", "values"),
    [
        (
            "max_path_length",
            {
                "max_path_length": 0,
                "max_read_bytes": 16,
                "max_write_bytes": 16,
            },
        ),
        (
            "max_read_bytes",
            {
                "max_path_length": 4096,
                "max_read_bytes": 0,
                "max_write_bytes": 16,
            },
        ),
        (
            "max_write_bytes",
            {
                "max_path_length": 4096,
                "max_read_bytes": 16,
                "max_write_bytes": 0,
            },
        ),
    ],
)
def test_limits_must_be_positive(
    workspace: Path,
    field: str,
    values: dict[str, int],
) -> None:
    with pytest.raises(
        ValueError,
        match=field,
    ):
        SafePathResolver(
            workspace,
            **values,
        )

def test_workspace_root_must_exist(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        PathTypeError,
        match="does not exist",
    ):
        SafePathResolver(
            tmp_path / "missing",
            max_path_length=4096,
            max_read_bytes=16,
            max_write_bytes=16,
        )

def test_workspace_root_must_be_directory(
    tmp_path: Path,
) -> None:
    workspace_file = tmp_path / "workspace.txt"
    workspace_file.write_text(
        "not a directory",
        encoding="utf-8",
    )

    with pytest.raises(
        PathTypeError,
        match="must be a directory",
    ):
        SafePathResolver(
            workspace_file,
            max_path_length=4096,
            max_read_bytes=16,
            max_write_bytes=16,
        )

@pytest.mark.parametrize(
    "raw_path",
    [
        "",
        "   ",
        "a" * 20,
        "bad\x00path.txt",
    ],
)
def test_invalid_raw_paths_are_rejected(
    workspace: Path,
    raw_path: str,
) -> None:
    resolver = SafePathResolver(
        workspace,
        max_path_length=10,
        max_read_bytes=16,
        max_write_bytes=16,
    )

    with pytest.raises(UnsafePathError):
        resolver.resolve_write_target(raw_path)

def test_non_string_path_is_rejected(
    resolver: SafePathResolver,
) -> None:
    with pytest.raises(
        UnsafePathError,
        match="must be a string",
    ):
        resolver.resolve_write_target(123)  # type: ignore[arg-type]

def test_to_relative_rejects_path_outside_workspace(
    resolver: SafePathResolver,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text(
        "outside",
        encoding="utf-8",
    )

    with pytest.raises(UnsafePathError):
        resolver.to_relative(outside)