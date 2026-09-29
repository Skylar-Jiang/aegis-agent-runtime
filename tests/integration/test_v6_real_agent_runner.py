from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_v6_runner_reports_non_secret_live_model_availability() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            root / "experiments" / "v2" / "runners" / "run_v6_real_agent_e2e.py",
            "--check-availability",
            "--repetitions",
            "1",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert set(payload) == {"configured", "model", "base_url_origin"}
    assert isinstance(payload["configured"], bool)
    assert "sk-" not in completed.stdout.casefold()
