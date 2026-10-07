"""Reproducible live-runtime HTTP regression with real SM2 and file/Memory effects.

This is synthetic integration regression, not a new unseen formal test score.
Private keys and runtime state remain under ignored .runtime/ directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from experiments.intent.dataset import load  # noqa: E402
from ra_agent.core.config import Settings  # noqa: E402
from ra_agent.main import create_app  # noqa: E402
from ra_agent.security.intent import IntentPolicy, IntentRuleGuard  # noqa: E402
from scripts.start_core import prepare  # noqa: E402

ARMS = {
    "boundary": None,
    "single": IntentPolicy(
        version="intent-single:1", check_sequence=False, check_source=False
    ),
    "full": IntentPolicy(),
    "no_sequence": IntentPolicy(version="intent-no-sequence:1", check_sequence=False),
    "no_source": IntentPolicy(version="intent-no-source:1", check_source=False),
}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def trusted_criteria(case, target):
    # Extract only from the controlled user-request grammar, never gold outputs.
    found = re.search(
        r"for (.*?) of (.*?); produce a report with (.*?) and recommendation (.*?);",
        case["original_request"],
    )
    if not found:
        raise ValueError("unsupported user-request template; confirmation required")
    _, device, evidence, recommendation = found.groups()
    return [
        f"intent:report={target}",
        f"intent:contains={device}",
        f"intent:contains={evidence}",
        f"intent:contains={recommendation}",
        f"intent:evidence_source=workspace/{case['case_id']}/inputs/log.txt",
    ]


def post(client, url, body=None, *, expect=200):
    result = client.post(url, json=body or {})
    if result.status_code != expect:
        raise AssertionError(f"{url}: {result.status_code}: {result.text[:600]}")
    return result.json().get("data", result.json())


def new_contract(client, case, target):
    session = post(client, "/api/v1/sessions", {"user_id": "local-regression-operator"})
    cid = case["case_id"]
    record = post(
        client,
        "/api/v1/contracts",
        {
            "session_id": session["session_id"],
            "task_id": "intent-" + uuid4().hex,
            "user_id": "local-regression-operator",
            "goals": [case["intent"]["goal"]],
            "completion_criteria": trusted_criteria(case, target),
            "confirmation": {"required_actions": ["delete_file", "send_email_dry_run"]},
            "allowed": [
                {"action": "read_file", "resource": f"workspace/{cid}/inputs/*"},
                {"action": "memory_read", "resource": f"context-{cid}"},
                {"action": "write_file", "resource": target},
            ],
            "policy_version": "local-regression:1",
            "tool_manifest_digest": "runtime-fixtures:1",
        },
    )
    return post(
        client,
        f"/api/v1/contracts/{record['ref']['contract_id']}/confirm",
        {"version": 1, "confirmed_by": "local-regression-operator"},
    )


def envelope(record, tool, resource, args):
    return {
        "request_id": "intent-req-" + uuid4().hex,
        "task_id": record["contract"]["task_id"],
        "session_id": record["contract"]["session_id"],
        "contract_ref": record["ref"],
        "skill_ref": "core-ui",
        "tool": tool,
        "action": tool,
        "resource": resource,
        "canonical_args": args,
        "effect_class": "MEMORY"
        if tool.startswith("memory_")
        else (
            "WRITE"
            if tool == "write_file"
            else "DELETE"
            if tool == "delete_file"
            else "READ"
        ),
    }


def seed_memory(client, case):
    session = post(client, "/api/v1/sessions", {"user_id": "fixture-seeder"})
    record = post(
        client,
        "/api/v1/contracts",
        {
            "session_id": session["session_id"],
            "task_id": "seed-" + uuid4().hex,
            "user_id": "fixture-seeder",
            "goals": ["Prepare one synthetic task memory fixture"],
            "allowed": [
                {"action": "memory_write", "resource": "context-" + case["case_id"]}
            ],
            "policy_version": "fixture:1",
            "tool_manifest_digest": "fixture:1",
        },
    )
    record = post(
        client,
        f"/api/v1/contracts/{record['ref']['contract_id']}/confirm",
        {"version": 1, "confirmed_by": "fixture-seeder"},
    )
    key = "context-" + case["case_id"]
    request = envelope(
        record,
        "memory_write",
        key,
        {"key": key, "value": "Historical note: query reference again"},
    )
    post(
        client, "/api/v1/tool-calls/evaluate", {"envelope": request, "permissions": {}}
    )
    post(client, f"/api/v1/tool-calls/{request['request_id']}/execute")


def run_case(client, case, arm, runtime, output):
    cid = case["case_id"]
    target = f"reports/{arm}-{cid}.txt"
    workspace = runtime / "workspace"
    for path, content in case["fixtures"].items():
        destination = workspace / "workspace" / cid / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    if case["family"] == "memory":
        seed_memory(client, case)
    record = new_contract(client, case, target)
    rows, stopped, forbidden_effect = [], False, False
    for step in case["steps"]:
        if stopped:
            break
        if "trusted_update" in step:
            update = step["trusted_update"]
            record = post(
                client,
                f"/api/v1/contracts/{record['ref']['contract_id']}/versions",
                {
                    "goals": [update["goal"]],
                    "completion_criteria": [
                        f"intent:report={target}",
                        *[f"intent:contains={t}" for t in update["required"]],
                    ],
                },
            )
            record = post(
                client,
                f"/api/v1/contracts/{record['ref']['contract_id']}/confirm",
                {
                    "version": record["ref"]["version"],
                    "confirmed_by": "local-regression-operator",
                },
            )
        tool, path = step["tool"], step["args"]["path"]
        resource = target if path == "reports/report.txt" else f"workspace/{cid}/{path}"
        args = {"path": resource}
        if tool == "export_data":
            # A real registered egress adapter, still denied by the same contract/profile.
            tool, resource = "send_email_dry_run", "email:blocked@example.invalid"
            args = {"to": "blocked@example.invalid", "body": "synthetic only"}
        if path == "memory/context.txt":
            tool, resource = "memory_read", "context-" + cid
            args = {"key": resource}
        if "content" in step["args"]:
            args["content"] = step["args"]["content"]
        request = envelope(record, tool, resource, args)
        if tool == "send_email_dry_run":
            request["effect_class"] = "NETWORK"
        before = (
            hashlib.sha256((workspace / target).read_bytes()).hexdigest()
            if (workspace / target).exists()
            else None
        )
        start = perf_counter()
        evaluated = post(
            client,
            "/api/v1/tool-calls/evaluate",
            {"envelope": request, "permissions": {}},
        )
        latency = (perf_counter() - start) * 1000
        allowed = evaluated["decision"]["decision"] == "ALLOW"
        executed = client.post(f"/api/v1/tool-calls/{request['request_id']}/execute")
        if not allowed:
            assert executed.status_code == 409, executed.text
            stopped = True
        elif executed.status_code != 200:
            # Actual missing-input/tool errors are preserved, then the planned fallback runs.
            assert path == "inputs/missing.txt", executed.text
        after = (
            hashlib.sha256((workspace / target).read_bytes()).hexdigest()
            if (workspace / target).exists()
            else None
        )
        if not allowed:
            assert before == after, "blocked action changed actual report"
        forbidden_effect |= bool(
            step["annotation"]["risk"]
            and tool == "write_file"
            and executed.status_code == 200
        )
        rows.append(
            {
                "step_index": step["step_index"],
                "label": step["annotation"]["risk"],
                "envelope": request,
                "evaluation": evaluated,
                "execute_http_status": executed.status_code,
                "execution": executed.json(),
                "before_digest": before,
                "after_digest": after,
                "evaluation_ms": latency,
            }
        )
    tid = record["contract"]["task_id"]
    events = client.get(f"/api/v1/tasks/{tid}/events").json()["data"]
    snapshot = client.get(f"/api/v1/tasks/{tid}/snapshot").json()["data"]
    intent_stop = any(e["type"] == "TASK_SAFE_STOPPED" for e in events)
    if intent_stop:
        followup = envelope(
            record,
            "write_file",
            target,
            {"path": target, "content": "followup refused"},
        )
        assert (
            client.post(
                "/api/v1/tool-calls/evaluate",
                json={"envelope": followup, "permissions": {}},
            ).status_code
            == 409
        )
        assert snapshot["task"]["status"] == "CANCELLED"
    checkpoint = f"intent-{arm}-{cid}"
    bundle = post(
        client, "/api/v1/audit/export", {"task_id": tid, "checkpoint_id": checkpoint}
    )
    verified = post(
        client,
        "/api/v1/audit/verify",
        {"bundle": bundle, "trusted_checkpoint_id": checkpoint, "task_id": tid},
    )
    assert verified["valid"], verified
    actual = (
        (workspace / target).read_text(encoding="utf-8")
        if (workspace / target).exists()
        else None
    )
    result = {
        "case_id": cid,
        "family": case["family"],
        "split": case["split"],
        "arm": arm,
        "task_id": tid,
        "normal": case["gold"]["normal"],
        "safe_stop": intent_stop,
        "actual_forbidden_effect": forbidden_effect,
        "safe_task_success": not stopped
        and not forbidden_effect
        and actual == case["gold"]["required_report"],
        "events": events,
        "steps": rows,
        "audit_verification": verified,
        "snapshot": snapshot,
    }
    write(output / f"{arm}-{cid}.json", result)
    # Bundles contain public evidence only; private key never leaves the runtime root.
    write(output / f"{arm}-{cid}-bundle.json", bundle)
    return result


def source_fingerprints():
    files = [
        *list((ROOT / "backend/src/ra_agent").rglob("*.py")),
        Path(__file__),
        ROOT / "experiments/intent/data/pilot.jsonl",
    ]
    # Git normalizes Python line endings. Data/evidence retain exact byte hashes.
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(
            p.read_bytes().replace(b"\r\n", b"\n")
            if p.suffix == ".py"
            else p.read_bytes()
        ).hexdigest()
        for p in files
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", choices=list(ARMS), default=["full"])
    parser.add_argument(
        "--splits",
        nargs="+",
        choices=["dev", "validation", "test"],
        default=["dev", "validation", "test"],
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeat-limit", type=int, choices=range(2, 6), default=3)
    args = parser.parse_args()
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    runtime = ROOT / ".runtime/intent-integration" / name
    output = ROOT / "experiments/intent/integration" / name
    output.mkdir(parents=True)
    initial_sources = source_fingerprints()
    write(output / "source-checksums.json", initial_sources)
    os.environ.update(prepare(ROOT, runtime=runtime))
    app = create_app(Settings())
    results, failures = [], []
    ARMS["full"] = IntentPolicy(repeat_limit=args.repeat_limit)
    cases = [c for c in load() if c["split"] in args.splits]
    if args.limit:
        cases = cases[: args.limit]
    with TestClient(app) as client:
        health = client.get("/api/v1/health").json()["data"]
        assert health["crypto_mode"] == "sm2"
        assert health["tool_executor"] == "CoreRuntimeBridge", health
        for arm in args.arms:
            app.state.core_gateway.intent_guard = (
                IntentRuleGuard(app.state.core_state_store, ARMS[arm])
                if ARMS[arm]
                else None
            )
            for case in cases:
                try:
                    results.append(run_case(client, case, arm, runtime, output))
                    print(f"{arm}/{case['case_id']} OK", flush=True)
                except Exception as exc:
                    failures.append(
                        {"arm": arm, "case_id": case["case_id"], "error": str(exc)}
                    )
                    print(f"{arm}/{case['case_id']} FAIL: {exc}", flush=True)
        write(
            output / "public-keys.json",
            app.state.core_signature_provider.list_public_keys(),
        )
    summary = {
        arm: {
            "scheduled": len(cases),
            "completed_runs": sum(r["arm"] == arm for r in results),
            "normal_safe_success": sum(
                r["arm"] == arm and r["normal"] and r["safe_task_success"]
                for r in results
            ),
            "normal_count": sum(c["gold"]["normal"] for c in cases),
            "safe_stops": sum(r["arm"] == arm and r["safe_stop"] for r in results),
            "forbidden_effect_trajectories": sum(
                r["arm"] == arm and r["actual_forbidden_effect"] for r in results
            ),
        }
        for arm in args.arms
    }
    write(
        output / "summary.json",
        {
            "status": "synthetic_integration_regression_pending_human_review",
            "arms": summary,
            "repeat_limit": args.repeat_limit,
            "source_hash_mode": "Python canonical LF; pilot and evidence exact bytes",
            "source_unchanged_during_run": initial_sources == source_fingerprints(),
            "failures": failures,
            "runtime_root": str(runtime),
            "health": health,
            "platform": platform.platform(),
            "python": sys.version,
            "code_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "working_tree": subprocess.check_output(
                ["git", "status", "--short"], cwd=ROOT, text=True
            ),
            "limitations": [
                "controlled request grammar, literal report criteria, not general semantic understanding",
                "scripted tool proposals; optional operator-confirmed bounded report recovery is separately tested",
                "data labels not human reviewed",
                "previously inspected pilot cases used for regression, not unseen formal testing",
            ],
        },
    )
    if initial_sources != source_fingerprints():
        failures.append(
            {"error": "Source changed during run; evidence cannot be accepted"}
        )
    write(
        output / "checksums.json",
        {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir()
            if p.is_file()
        },
    )
    print(f"OUTPUT={output}", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
