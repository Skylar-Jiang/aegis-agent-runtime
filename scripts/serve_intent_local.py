"""Open an existing isolated integration runtime for UI review; never reset it."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))

from start_core import prepare  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if not runtime.is_relative_to(ROOT / ".runtime/intent-integration"):
        parser.error(
            "runtime must be an isolated .runtime/intent-integration directory"
        )
    if not (runtime / "state.sqlite3").exists():
        parser.error("existing integration state is required")
    os.environ.update(prepare(ROOT, runtime=runtime))
    import uvicorn

    uvicorn.run("ra_agent.main:app", host="127.0.0.1", port=args.port)
