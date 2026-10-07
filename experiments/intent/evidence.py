"""Verify evidence checksums; no signature/authorship claims."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(folder):
    folder = Path(folder)
    checks = json.loads((folder / "checksums.json").read_text(encoding="utf-8"))
    for name, expected in checks.items():
        if Path(name).name != name:
            raise ValueError("invalid evidence filename")
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"checksum mismatch: {name}")
    return len(checks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    print(f"Verified files: {verify(parser.parse_args().folder)}")
