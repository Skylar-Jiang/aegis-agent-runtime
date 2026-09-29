import json
import os
import subprocess
import sys
from pathlib import Path


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "ra_agent.audit", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(Path(__file__).parents[3] / "backend/src"),
        },
    )


def test_cli_invalid_input_is_machine_readable(tmp_path):
    keys = tmp_path / "keys.json"
    keys.write_text('{"duplicate":1,"duplicate":2}', encoding="utf-8")
    result = run_cli(
        "--bundle",
        str(tmp_path / "missing.json"),
        "--keys",
        str(keys),
        "--checkpoints",
        str(tmp_path),
        "--checkpoint-id",
        "cp",
        "--task-id",
        "task",
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["valid"] is False
    assert "Traceback" not in result.stderr


def test_cli_verifies_frozen_fixture_without_private_key():
    fixture = Path(__file__).parents[2] / "fixtures/core/crypto"
    result = run_cli(
        "--bundle",
        str(fixture / "bundle.json"),
        "--keys",
        str(fixture / "public_keys.json"),
        "--checkpoints",
        str(fixture / "trusted_checkpoints"),
        "--checkpoint-id",
        "fixture-cp-1",
        "--task-id",
        "fixture-task-1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_demo_runs_gateway_eventstore_and_offline_verifier(tmp_path):
    root = Path(__file__).parents[3]
    output = tmp_path / "demo"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/demo_core_crypto.py"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(root / "backend/src"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["pipeline"] == "Gateway->JsonlEventStore->AuditExportService->AuditVerifier"
    assert summary["normal"]["valid"] is True
    assert summary["tampered"]["valid"] is False

    bundle = json.loads((output / "bundle.json").read_text(encoding="utf-8"))
    # PR2 records both the initial decision and the pre-execution TOCTOU recheck,
    # followed by execution start/finish. Keep this assertion semantic so a
    # future event schema change cannot silently remove a required boundary event.
    assert [entry["event"]["type"] for entry in bundle["entries"]] == [
        "PERMISSION_EVALUATED",
        "GATEWAY_ALLOWED",
        "PERMISSION_EVALUATED",
        "GATEWAY_ALLOWED",
        "EXECUTION_STARTED",
        "EXECUTION_FINISHED",
    ]
    assert {obj["envelope"]["object_type"] for obj in bundle["objects"]} == {
        "ToolCallEnvelope",
        "GatewayDecision",
        "ToolResult",
    }
