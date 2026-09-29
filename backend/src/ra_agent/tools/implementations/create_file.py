from pathlib import Path

from .write_file import WriteFileHandler


class CreateFileHandler(WriteFileHandler):
    """Stage a new UTF-8 file while refusing to overwrite an existing target."""

    TOOL_NAME = "create_file"

    @staticmethod
    def _validate_target_state(target: Path) -> None:
        if target.exists() or target.is_symlink():
            raise FileExistsError("create_file target already exists")
