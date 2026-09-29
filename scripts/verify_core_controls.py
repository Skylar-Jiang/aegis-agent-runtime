"""Exercise real Core HTTP controls and inspect actual files and Runtime receipts.

Start scripts/start_core.py first. Uses its own profile/tasks and new filenames;
existing profiles, files and evidence are preserved. No mocked HTTP or signatures.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default=os.environ.get("AEGIS_BROWSER_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(
            os.environ.get(
                "AEGIS_BROWSER_WORKSPACE", str(root / ".runtime/core-demo/workspace")
            )
        ),
    )
    parser.add_argument(
        "--output", type=Path, default=root / ".runtime/review-fixes/controls"
    )
    args = parser.parse_args()
    run_id = uuid.uuid4().hex
    profile_id = f"controls-{run_id}"
    results: list[dict[str, object]] = []
    trace: list[dict[str, object]] = []
    args.output.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.url, timeout=60) as client:

        def send(method, path, body=None):
            response = client.request(method, path, json=body)
            trace.append(
                {
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "response": response.json(),
                }
            )
            return response

        def api(method, path, body=None):
            response = send(method, path, body)
            response.raise_for_status()
            return response.json()["data"]

        health = api("GET", "/api/v1/health")
        assert health["crypto_mode"] == "sm2", "Real SM2 backend is required"
        profile = {
            "allowed_actions": ["create_file", "write_file", "read_file", "list_dir"],
            "resource_scopes": ["reports/**"],
            "allow_egress": False,
            "max_affected_objects": 100,
            "approval_policy": {"required_actions": []},
        }
        api("PUT", f"/api/security-profiles/{profile_id}", profile)
        session = api(
            "POST",
            "/api/v1/sessions",
            {
                "user_id": profile_id,
                "security_profile_id": profile_id,
                "title": "真实控制实验",
            },
        )

        def task(changes=None):
            draft = api(
                "POST",
                f"/api/v1/sessions/{session['session_id']}/tasks",
                {"objective": "验证真实工具执行、权限边界和可恢复证据"},
            )
            record = draft["contract"]
            if changes:
                record = api(
                    "POST",
                    f"/api/v1/contracts/{record['ref']['contract_id']}/versions",
                    changes,
                )
            ref = api(
                "POST",
                f"/api/v1/contracts/{record['ref']['contract_id']}/confirm",
                {"version": record["ref"]["version"], "confirmed_by": profile_id},
            )["ref"]
            return draft["task_id"], ref

        def request(task_id, ref, name, *, tool="write_file", content="真实实验内容"):
            resource = f"reports/{run_id}-{name}.txt"
            return {
                "envelope": {
                    "request_id": f"control-{uuid.uuid4().hex}",
                    "task_id": task_id,
                    "session_id": session["session_id"],
                    "contract_ref": ref,
                    "skill_ref": "core-ui",
                    "tool": tool,
                    "action": tool,
                    "effect_class": "READ" if tool == "read_file" else "WRITE",
                    "resource": resource,
                    "canonical_args": {"path": resource, "content": content},
                },
                "permissions": {
                    "user_grants": [],
                    "skill_grants": [],
                    "system_grants": [],
                },
            }

        # September scan regressions: use real HTTP, real SM2 and inspect files.
        quota_task, quota_ref = task({"limits": {}})
        quota_body = request(quota_task, quota_ref, "tightening", content="hello")
        api("POST", "/api/v1/tool-calls/evaluate", quota_body)
        api(
            "POST", f"/api/v1/tool-calls/{quota_body['envelope']['request_id']}/execute"
        )
        cid = quota_ref["contract_id"]
        version = api(
            "POST", f"/api/v1/contracts/{cid}/versions", {"limits": {"max_bytes": 5}}
        )["ref"]["version"]
        newer = api(
            "POST",
            f"/api/v1/contracts/{cid}/confirm",
            {"version": version, "confirmed_by": profile_id},
        )["ref"]
        quota_body["envelope"].update(
            request_id=f"control-{uuid.uuid4().hex}", contract_ref=newer
        )
        quota_body["envelope"]["canonical_args"]["content"] = "!"
        api("POST", "/api/v1/tool-calls/evaluate", quota_body)
        rejected = send(
            "POST", f"/api/v1/tool-calls/{quota_body['envelope']['request_id']}/execute"
        )
        assert rejected.status_code == 409 and "LIMIT_EXCEEDED" in rejected.text
        assert (args.workspace / quota_body["envelope"]["resource"]).read_text(
            encoding="utf-8"
        ) == "hello"
        results.append(
            {
                "case": "unlimited_then_tightened_contract_preserves_usage",
                "passed": True,
                "status": 409,
            }
        )

        approval_task, approval_ref = task(
            {"confirmation": {"required_actions": ["write_file"]}}
        )
        approval_body = request(approval_task, approval_ref, "confirmation-retry")
        pending = api("POST", "/api/v1/tool-calls/evaluate", approval_body)
        repeated = api(
            "POST",
            f"/api/v1/contracts/{approval_ref['contract_id']}/confirm",
            {"version": approval_ref["version"], "confirmed_by": profile_id},
        )
        assert repeated["ref"] == approval_ref
        again = api("POST", "/api/v1/tool-calls/evaluate", approval_body)
        assert (
            again["decision"]["confirmation_id"]
            == pending["decision"]["confirmation_id"]
        )
        results.append({"case": "reconfirm_preserves_pending_approval", "passed": True})

        current_profile = api("PUT", f"/api/security-profiles/{profile_id}", profile)
        provenance = api("POST", "/api/v1/tool-calls/evaluate", approval_body)
        assert (
            provenance["decision"]["versions"]["policy"]
            == f"{profile_id}:{current_profile['version']}"
        )
        assert (
            provenance["decision"]["versions"]["contract_policy"]
            != provenance["decision"]["versions"]["policy"]
        )
        results.append(
            {
                "case": "decision_records_actual_policy_version",
                "passed": True,
                "versions": provenance["decision"]["versions"],
            }
        )

        growth_task, growth_ref = task()
        with ThreadPoolExecutor(max_workers=6) as pool:
            calls = [
                pool.submit(
                    api,
                    "POST",
                    "/api/v1/tool-calls/evaluate",
                    request(growth_task, growth_ref, f"growth-{i}"),
                )
                for i in range(6)
            ]
            exports = [
                pool.submit(
                    api,
                    "POST",
                    "/api/v1/audit/export",
                    {
                        "task_id": growth_task,
                        "checkpoint_id": f"growth-{uuid.uuid4().hex}",
                    },
                )
                for _ in range(3)
            ]
            for call_future in calls:
                call_future.result()
            packages = [future.result() for future in exports]
        for package in packages:
            verified = api(
                "POST",
                "/api/v1/audit/verify",
                {
                    "task_id": growth_task,
                    "trusted_checkpoint_id": package["checkpoint"]["checkpoint_id"],
                    "bundle": package,
                },
            )
            assert verified["valid"] is True
        page = api("GET", f"/api/v1/tasks/{growth_task}/events?limit=1000")
        assert len(page) == len(api("GET", f"/api/v1/tasks/{growth_task}/events"))
        results.append(
            {
                "case": "concurrent_event_growth_page_and_export",
                "passed": True,
                "verified_prefixes": len(packages),
                "events": len(page),
            }
        )

        restored = api("GET", f"/api/v1/tasks/{approval_task}/snapshot")
        assert (
            restored["latest_request"]["confirmation_id"]
            == pending["decision"]["confirmation_id"]
        )
        results.append({"case": "restore_pending_request_from_server", "passed": True})

        tid, ref = task()
        normal = request(tid, ref, "normal")
        decision = api("POST", "/api/v1/tool-calls/evaluate", normal)
        assert decision["decision"]["decision"] == "ALLOW"
        changed = json.loads(json.dumps(normal))
        changed["envelope"]["canonical_args"]["content"] = "unapproved replacement"
        assert send("POST", "/api/v1/tool-calls/evaluate", changed).status_code == 409
        results.append(
            {"case": "same_request_changed_parameters", "passed": True, "status": 409}
        )
        execution_path = (
            f"/api/v1/tool-calls/{normal['envelope']['request_id']}/execute"
        )
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: client.post(execution_path), range(4)))
        for response in responses:
            response.raise_for_status()
        executions = [response.json()["data"] for response in responses]
        assert all(item == executions[0] for item in executions)
        receipt = executions[0]["result"]["runtime"]
        assert receipt["commit_status"] == "COMMITTED" and receipt["checkpoint_id"]
        file = args.workspace / normal["envelope"]["resource"]
        assert file.read_text(encoding="utf-8") == "真实实验内容"
        events = api("GET", f"/api/v1/tasks/{tid}/events")
        assert sum(event["type"] == "EXECUTION_STARTED" for event in events) == 1
        results.append(
            {
                "case": "four_concurrent_retries",
                "passed": True,
                "execution_events": 1,
                "receipt": receipt,
                "file": file.name,
            }
        )
        effects = api("GET", f"/api/tasks/{tid}/effects")
        assert any(
            item["effect_id"] == receipt["effect_id"] and item["status"] == "COMMITTED"
            for item in effects
        )
        results.append(
            {"case": "shared_runtime_effects", "passed": True, "effects": effects}
        )

        for name, limits in [
            ("zero_objects", {"max_affected_objects": 0}),
            ("byte_limit", {"max_bytes": 1}),
        ]:
            limited_task, limited_ref = task({"limits": limits})
            body = request(limited_task, limited_ref, name)
            api("POST", "/api/v1/tool-calls/evaluate", body)
            response = send(
                "POST", f"/api/v1/tool-calls/{body['envelope']['request_id']}/execute"
            )
            assert response.status_code == 409 and "LIMIT" in response.text
            assert not (args.workspace / body["envelope"]["resource"]).exists()
            results.append(
                {"case": name, "passed": True, "status": 409, "file_created": False}
            )

        cancelled_task, cancelled_ref = task()
        cancelled = request(cancelled_task, cancelled_ref, "cancelled")
        api("POST", "/api/v1/tool-calls/evaluate", cancelled)
        api("POST", f"/api/tasks/{cancelled_task}/cancel")
        assert (
            send(
                "POST",
                f"/api/v1/tool-calls/{cancelled['envelope']['request_id']}/execute",
            ).status_code
            == 409
        )
        assert not (args.workspace / cancelled["envelope"]["resource"]).exists()
        results.append(
            {"case": "cancel_before_execution", "passed": True, "file_created": False}
        )

        other_task, _ = task()
        wrong_task = request(other_task, ref, "wrong-task")
        rejection = send("POST", "/api/v1/tool-calls/evaluate", wrong_task)
        assert (
            rejection.status_code == 409
            or rejection.json()["data"]["decision"]["decision"] == "DENY"
        )
        results.append({"case": "task_binding", "passed": True})

        api(
            "PUT",
            f"/api/security-profiles/{profile_id}",
            profile | {"max_affected_objects": 1},
        )
        for index, (tool, skill) in enumerate(
            [("create_file", "core-ui"), ("write_file", "writer")]
        ):
            body = request(tid, ref, f"quota-switch-{index}", tool=tool)
            body["envelope"]["skill_ref"] = skill
            api("POST", "/api/v1/tool-calls/evaluate", body)
            response = send(
                "POST", f"/api/v1/tool-calls/{body['envelope']['request_id']}/execute"
            )
            assert response.status_code == 409 and "LIMIT_EXCEEDED" in response.text
            assert not (args.workspace / body["envelope"]["resource"]).exists()
        results.append(
            {
                "case": "task_total_across_tools_and_skills",
                "passed": True,
                "file_created": False,
            }
        )
        api("PUT", f"/api/security-profiles/{profile_id}", profile)

        tightened = request(tid, ref, "after-tightening")
        api("POST", "/api/v1/tool-calls/evaluate", tightened)
        api(
            "PUT",
            f"/api/security-profiles/{profile_id}",
            profile | {"allowed_actions": ["read_file"]},
        )
        assert (
            send(
                "POST",
                f"/api/v1/tool-calls/{tightened['envelope']['request_id']}/execute",
            ).status_code
            == 409
        )
        assert not (args.workspace / tightened["envelope"]["resource"]).exists()
        results.append(
            {
                "case": "policy_change_before_execution",
                "passed": True,
                "file_created": False,
            }
        )
        api("PUT", f"/api/security-profiles/{profile_id}", profile)

        if os.name == "nt":
            new_ref = api(
                "POST",
                f"/api/v1/contracts/{ref['contract_id']}/versions",
                {
                    "denied": [
                        {
                            "tool": "read_file",
                            "action": "read_file",
                            "effect": "READ",
                            "resource": normal["envelope"]["resource"],
                        }
                    ]
                },
            )["ref"]
            new_ref = api(
                "POST",
                f"/api/v1/contracts/{ref['contract_id']}/confirm",
                {"version": new_ref["version"], "confirmed_by": profile_id},
            )["ref"]
            uppercase = request(tid, new_ref, "normal", tool="read_file")
            uppercase["envelope"]["resource"] = uppercase["envelope"][
                "resource"
            ].upper()
            uppercase["envelope"]["canonical_args"] = {
                "path": uppercase["envelope"]["resource"]
            }
            denied = api("POST", "/api/v1/tool-calls/evaluate", uppercase)
            assert denied["decision"]["decision"] == "DENY"
            results.append({"case": "windows_case_identity", "passed": True})

        checkpoint = f"controls-{uuid.uuid4().hex}"
        bundle = api(
            "POST",
            "/api/v1/audit/export",
            {"task_id": tid, "checkpoint_id": checkpoint},
        )
        original = api(
            "POST",
            "/api/v1/audit/verify",
            {"task_id": tid, "trusted_checkpoint_id": checkpoint, "bundle": bundle},
        )
        assert original["valid"] is True
        altered = json.loads(json.dumps(bundle))
        altered["entries"][0]["event"]["source_ref"] = "modified experiment copy"
        invalid = api(
            "POST",
            "/api/v1/audit/verify",
            {"task_id": tid, "trusted_checkpoint_id": checkpoint, "bundle": altered},
        )
        assert invalid["valid"] is False
        results.append(
            {
                "case": "sm2_sm3_evidence",
                "passed": True,
                "original": original,
                "tampered": invalid,
            }
        )
        history = api("GET", f"/api/tasks/{tid}/report")
        assert history["total_events"] == len(api("GET", f"/api/v1/tasks/{tid}/events"))
        result = {
            "created_at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "url": args.url,
            "health": health,
            "results": results,
            "status": "passed",
            "task_id": tid,
            "checkpoint_id": checkpoint,
        }
        (args.output / f"{run_id}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (args.output / f"{run_id}-trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": "passed",
                    "cases": len(results),
                    "output": str(args.output / f"{run_id}.json"),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
