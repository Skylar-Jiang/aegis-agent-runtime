"""Deterministic telecom fixture replay with real local effects behind Core.

This is a mock detector adapter, not a detection algorithm or production planner.
Only fixed, version-controlled steps can enter the adapter; no arbitrary payload API.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from ra_agent.contracts.core_service import ContractService
from ra_agent.contracts.core_v1 import (
    ContractPermissionRule,
    ContractRecord,
    EffectClass,
    GatewayDecisionType,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
    ToolCallEnvelope,
)
from ra_agent.events import JsonlEventStore
from ra_agent.gateway import GatewayError, ToolGateway
from ra_agent.gateway.adapters import AdapterBypassError, CoreToolExecutor, ToolExecutionRejected
from ra_agent.permissions import PermissionResolver
from ra_agent.tools.path_resolver import SafePathResolver

from .contracts import BehaviorEvent, CorrectionPlan, DecisionResult, EffectCheck, IntentSpec
from .contracts import TaskContract as IntentTaskContract

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "fixtures/telecom"
TOOLS = {
    "read_file": EffectClass.READ,
    "read_knowledge_base": EffectClass.READ,
    "write_file": EffectClass.WRITE,
    "memory_read": EffectClass.READ,
    "memory_write": EffectClass.MEMORY,
    "restore_trusted_memory": EffectClass.MEMORY,
    "sim_config_modify": EffectClass.WRITE,
    "sim_report_send": EffectClass.NETWORK,
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class TelecomExecutor(CoreToolExecutor):
    """Real Core files/Memory plus two local-only simulated endpoints."""

    def __init__(self, root: Path) -> None:
        super().__init__(
            path_resolver=SafePathResolver(
                root, max_path_length=4096, max_read_bytes=1024 * 1024, max_write_bytes=1024 * 1024
            ),
            memory_path=root / "memory.json",
            outbox_path=root / "unused-outbox.jsonl",
        )
        self.root = root
        self.admitted: set[str] = set()

    async def execute(
        self, envelope: ToolCallEnvelope, *, gateway_token: object | None = None
    ) -> dict[str, Any]:
        if self._gateway_token is None or gateway_token is not self._gateway_token:
            raise AdapterBypassError("telecom adapter requires Core ToolGateway")
        if envelope.request_id not in self.admitted:
            raise ToolExecutionRejected("fixture Intent admission is missing")
        if envelope.tool == "restore_trusted_memory":
            self._save_memory(envelope.canonical_args["value"])
            return {"tool": envelope.tool, "restored": True}
        if envelope.tool == "read_knowledge_base":
            raw = self.path_resolver.read_file_bytes(envelope.resource)
            return {"tool": envelope.tool, **json.loads(raw)}
        if envelope.tool not in {"sim_config_modify", "sim_report_send"}:
            return await super().execute(envelope, gateway_token=gateway_token)
        payload = envelope.canonical_args
        calls = read_json(self.root / "endpoints.json")
        if envelope.tool == "sim_config_modify":
            if envelope.resource != "devices/router-a.cfg":
                raise ToolExecutionRejected("only the synthetic router target is available")
            path = self.path_resolver.resolve_existing_file(envelope.resource)
            path.write_text(payload["content"], encoding="utf-8")
            effect = "LOCAL_SYNTHETIC_CONFIG_CHANGED"
        else:
            if envelope.resource not in {"attacker@example.invalid", "noc-review@example.invalid"}:
                raise ToolExecutionRejected("only reserved synthetic recipients are available")
            effect = "LOCAL_OUTBOX_RECORDED_NO_DELIVERY"
        call = {
            "request_id": envelope.request_id,
            "tool": envelope.tool,
            "target": envelope.resource,
            "payload_digest": digest(payload),
            "effect": effect,
            "sent": False,
        }
        calls.append(call)
        (self.root / "endpoints.json").write_text(canonical(calls), encoding="utf-8")
        return call


class TelecomDemoService:
    def __init__(self, workspace: Path) -> None:
        self.root = workspace.resolve() / "telecom-demo"
        self.lock = asyncio.Lock()
        self.cases = {x["case_id"]: x for x in read_json(FIXTURES / "cases.json")["cases"]}
        self.runs: dict[str, dict[str, Any]] = {}

    def list_cases(self) -> list[dict[str, Any]]:
        return [
            {
                k: case[k]
                for k in ("case_id", "title", "original_task", "attack_input", "expected_state")
            }
            for case in self.cases.values()
        ]

    async def reset(self) -> dict[str, int]:
        async with self.lock:
            # Only this service's owned child is removable, never the supplied workspace.
            if self.root.is_symlink() or self.root.resolve() != self.root:
                raise ValueError("demo root cannot be a filesystem link")
            if self.root.exists():
                if not (self.root / ".member4-owned").is_file():
                    raise ValueError("refusing to reset a directory without the ownership marker")
                shutil.rmtree(self.root)
            self.runs.clear()
            return {"run_count": 0, "config_call_count": 0, "send_call_count": 0}

    def _run(self, run_id: str) -> dict[str, Any]:
        return self.runs[run_id]

    async def start(self, case_id: str) -> dict[str, Any]:
        async with self.lock:
            case = self.cases[case_id]
            if self.root.is_symlink() or self.root.resolve() != self.root:
                raise ValueError("demo root cannot be a filesystem link")
            if self.root.exists() and not (self.root / ".member4-owned").is_file():
                raise ValueError("demo root already exists without the ownership marker")
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / ".member4-owned").touch()
            run_id = "telecom-" + uuid4().hex
            root = self.root / run_id
            root.mkdir()
            for folder in ("devices", "tickets", "logs", "knowledge"):
                shutil.copytree(FIXTURES / folder, root / folder)
            shutil.copyfile(FIXTURES / "memory.json", root / "memory.json")
            (root / "endpoints.json").write_text("[]", encoding="utf-8")
            (root / "reports").mkdir()
            store = JsonlEventStore(root / "events.jsonl")
            contracts = ContractService()
            executor = TelecomExecutor(root)
            gateway = ToolGateway(
                contracts=contracts,
                resolver=PermissionResolver(),
                event_store=store,
                executor=executor,
            )
            run = {
                "run_id": run_id,
                "case": case,
                "root": root,
                "store": store,
                "contracts": contracts,
                "executor": executor,
                "gateway": gateway,
                "actions": [],
                "effect_checks": [],
                "intent_history": [],
                "contract_history": [],
                "core_contract_history": [],
                "reads": {},
                "status": "RUNNING",
                "correction_plan": None,
                "request_counter": 0,
            }
            self.runs[run_id] = run
            run["baseline"] = self._observe(run)
            run["committed"] = run["baseline"]
            record = await contracts.create_contract(
                TaskContractCreateRequest(
                    session_id=run_id,
                    task_id=run_id,
                    user_id="synthetic-operator",
                    goals=[case["original_task"]],
                    completion_criteria=["Local evidenced risk report"],
                    # Keep base permissions constant, including the simulated attack targets.
                    allowed=[
                        ContractPermissionRule(tool=tool, action=tool, resource="*", effect="*")
                        for tool in TOOLS
                    ],
                    policy_version="demo-policy:0.1",
                    tool_manifest_digest=digest(sorted(TOOLS)),
                )
            )
            record = await contracts.confirm_contract(
                record.ref.contract_id, version=1, confirmed_by="synthetic-operator"
            )
            await self._version(run, record)
            await self._event(run, "TASK_STARTED", "RUNNING", "user:original-task")
            for step in case["steps"]:
                if not await self._action(run, step):
                    break
            else:
                run["status"] = (
                    "WAITING_GOAL_CONFIRMATION"
                    if case_id == "legitimate_goal_change"
                    else "COMPLETED"
                )
                await self._event(run, run["status"], run["status"], "user:goal-change")
            return await self._view(run)

    async def _version(self, run: dict[str, Any], record: ContractRecord) -> None:
        version = record.ref.version
        if version == 2:
            run["contract_history"][-1]["status"] = "SUPERSEDED"
            previous = run["contract_history"][-1]
            previous["digest"] = digest({k: v for k, v in previous.items() if k != "digest"})
        goal = record.contract.goals[0]
        spec = IntentSpec(
            intent_id=run["run_id"] + "-intent",
            task_id=run["run_id"],
            goal=goal,
            scope=["synthetic router-a", "local report", "analysis Memory"],
            allowed_actions=["read_file", "write_file", "memory_read", "memory_write"]
            + (["sim_report_send"] if version == 2 else []),
            forbidden_actions=["sim_config_modify"] + ([] if version == 2 else ["sim_report_send"]),
            success_criteria=["Report cites source evidence", "Configuration preserved"],
            source_refs=["user:original-task"] + (["user:goal-change"] if version == 2 else []),
            version=version,
            confirmed_by="synthetic-operator",
            confirmed_at=datetime.now(UTC),
        )
        payload = dict(
            contract_id=record.ref.contract_id,
            contract_version=version,
            intent_ref=f"{spec.intent_id}:v{version}",
            resources=spec.scope,
            tools=spec.allowed_actions,
            permissions={
                "devices/router-a.cfg": "read",
                "reports/risk-report.md": "write",
                "memory:analysis": "write",
                **({"noc-review@example.invalid": "simulated_send"} if version == 2 else {}),
            },
            completion_conditions=spec.success_criteria,
            parent_version=version - 1 if version > 1 else None,
            status="CONFIRMED",
        )
        contract = IntentTaskContract.model_validate({**payload, "digest": digest(payload)})
        run["intent_history"].append(spec.model_dump(mode="json"))
        run["contract_history"].append(contract.model_dump(mode="json"))
        run["core_contract_history"].append(record.model_dump(mode="json"))
        run["record"] = record
        await run["gateway"].record_contract_event(
            record, event_type="CONTRACT_CONFIRMED", actor="synthetic-operator"
        )

    def _report(self, run: dict[str, Any]) -> str:
        reads = run["reads"]
        return "\n".join(
            [
                "# SYNTHETIC router-a risk report",
                "Ticket: INC-SYN-1042",
                "",
                "Risks: BGP hold-time 3; disabled remote logging; broad management ACL.",
                "KB-SYN-002 supports logging and management access review.",
                "Recommend an approved maintenance change; no device modification performed.",
                "",
                *[
                    f"## Source {path}\n{reads[path]}"
                    for path in (
                        "devices/router-a.cfg",
                        "tickets/INC-SYN-1042.json",
                        "logs/fault.log",
                        "knowledge/safe.md",
                    )
                ],
                "",
            ]
        )

    async def _action(self, run: dict[str, Any], step: dict[str, Any]) -> bool:
        run["request_counter"] += 1
        request_id = f"{run['run_id']}-step-{run['request_counter']}"
        args = dict(step["arguments"])
        if args.pop("generated_report", False):
            args = {"path": step["target"], "content": self._report(run)}
        if args.pop("generated_analysis", False):
            report_digest = hashlib.sha256(
                (run["root"] / "reports/risk-report.md").read_bytes()
            ).hexdigest()
            args = {
                "key": "analysis",
                "value": {
                    "report_sha256": report_digest,
                    "trusted": True,
                    "source_refs": sorted(run["reads"]),
                },
            }
        envelope = ToolCallEnvelope(
            request_id=request_id,
            task_id=run["run_id"],
            session_id=run["run_id"],
            contract_ref=run["record"].ref,
            skill_ref="telecom-synthetic",
            tool=step["tool"],
            action=step["tool"],
            canonical_args=args,
            resource=step["target"],
            effect_class=TOOLS[step["tool"]],
        )
        grants = [
            PermissionGrant(
                subject="*",
                skill="*",
                tool=tool,
                action=tool,
                resource="*",
                effect=GrantEffect.ALLOW,
                scope=GrantScope.GLOBAL,
                source="demo",
            )
            for tool in TOOLS
        ]
        evaluation = await run["gateway"].evaluate(
            envelope,
            PermissionContext(user_grants=grants, skill_grants=grants, system_grants=grants),
        )
        behavior = BehaviorEvent(
            event_id=request_id + "-behavior",
            task_id=run["run_id"],
            step_index=run["request_counter"],
            actor="scripted-synthetic-planner",
            source_type=step["source_type"],
            source_ref=step["source_ref"],
            subgoal=step["subgoal"],
            tool=envelope.tool,
            action=envelope.action,
            args_digest=digest(args),
            target=envelope.resource,
            risk_flags=step["mock_decision"]["trigger_dimensions"],
            timestamp=datetime.now(UTC),
        )
        decision = DecisionResult(
            decision_id=request_id + "-decision",
            **step["mock_decision"],
            policy_version="demo-policy:0.1",
            detector_version="fixture/mock:0.1",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        action = {
            "request_id": request_id,
            "behavior_event": behavior.model_dump(mode="json"),
            "decision_result": decision.model_dump(mode="json"),
            "gateway_decision": evaluation.decision.model_dump(mode="json"),
            "envelope": envelope.model_dump(mode="json"),
            "execution_status": "NOT_EXECUTED",
            "result": None,
        }
        run["actions"].append(action)
        await self._event(run, "BEHAVIOR_OBSERVED", "PROPOSED", step["source_ref"], request_id)
        await self._event(run, "INTENT_DECIDED", decision.decision, step["source_ref"], request_id)
        before = self._observe(run)
        allowed = decision.decision == "ALLOW" and datetime.now(UTC) < decision.expires_at
        if allowed and evaluation.decision.decision is GatewayDecisionType.ALLOW:
            run["executor"].admitted.add(request_id)
            execution = await run["gateway"].execute(request_id)
            action["result"] = execution.result
            failed = execution.result.get("ok") is False
            action["execution_status"] = "FAILED" if failed else execution.status
            if envelope.tool == "read_file":
                run["reads"][envelope.resource] = execution.result["content"]
            if failed:
                await self._event(
                    run, "TOOL_FEEDBACK_OBSERVED", "FAILED", step["source_ref"], request_id
                )
        else:
            run["status"] = {
                "BLOCK": "BLOCKED",
                "CLARIFY": "WAITING_CLARIFICATION",
                "REPLAN": "WAITING_REPLAN",
            }.get(decision.decision, "BLOCKED")
            if decision.decision in {"REPLAN", "CLARIFY"}:
                plan = CorrectionPlan(
                    plan_id=request_id + "-plan",
                    base_contract_version=run["record"].ref.version,
                    contaminated_refs=decision.evidence_refs,
                    proposed_actions=(
                        [{"tool": "restore_trusted_memory", "target": "memory.json"}]
                        if decision.decision == "CLARIFY"
                        else [
                            {"tool": "read_file", "target": "knowledge/safe.md"},
                            {"tool": "write_file", "target": "reports/risk-report.md"},
                            {"tool": "memory_write", "target": "analysis"},
                        ]
                    ),
                    added_scope=[],
                    confirmation_required=decision.decision == "CLARIFY",
                    retry_budget=1,
                    status="PROPOSED",
                )
                run["correction_plan"] = plan.model_dump(mode="json")
                await self._event(
                    run, "CORRECTION_PROPOSED", run["status"], "system:trusted-plan", request_id
                )
        after = self._observe(run)
        await self._effect(run, request_id, envelope.tool, envelope.resource, before, after)
        run["committed"] = after
        return allowed

    async def _effect(
        self,
        run: dict[str, Any],
        request_id: str,
        tool: str,
        target: str,
        before: dict,
        after: dict,
        *,
        mismatch: bool = False,
    ) -> None:
        changed = before != after
        check = EffectCheck(
            effect_id=request_id + f"-effect-{len(run['effect_checks'])}",
            request_id=request_id,
            tool=tool,
            normalized_target=target,
            before_digest=digest(before),
            after_digest=digest(after),
            side_effect_ref=f"local:{run['run_id']}:{target}" if changed else None,
            status="MISMATCH" if mismatch else "APPLIED" if changed else "UNCHANGED",
        )
        run["effect_checks"].append(check.model_dump(mode="json"))
        await self._event(
            run, "EFFECT_CHECKED", check.status, f"effect:{check.effect_id}", request_id
        )

    async def _event(
        self, run: dict[str, Any], kind: str, state: str, source: str, request_id: str | None = None
    ) -> None:
        events = await run["store"].list_task_events(run["run_id"])
        await run["store"].append_event(
            {
                "event_id": "event-" + uuid4().hex,
                "task_id": run["run_id"],
                "parent_event_id": events[-1]["event_id"] if events else None,
                "type": kind,
                "actor": "telecom-demo",
                "source_ref": source,
                "object_digest": None,
                "state": state,
                "decision": None,
                "result_digest": None,
                "occurred_at": datetime.now(UTC).isoformat(),
                "request_id": request_id,
            }
        )

    def _observe(self, run: dict[str, Any]) -> dict[str, Any]:
        root = run["root"]
        paths = sorted(
            str(p.relative_to(root).as_posix())
            for folder in ("devices", "tickets", "logs", "knowledge")
            for p in (root / folder).rglob("*")
            if p.is_file()
        ) + ["reports/risk-report.md"]
        files = {
            p: hashlib.sha256((root / p).read_bytes()).hexdigest() if (root / p).exists() else None
            for p in paths
        }
        calls = read_json(root / "endpoints.json")
        return {
            "files": files,
            "memory": read_json(root / "memory.json"),
            "endpoint_calls": calls,
            "config_call_count": sum(x["tool"] == "sim_config_modify" for x in calls),
            "send_call_count": sum(x["tool"] == "sim_report_send" for x in calls),
        }

    def _acceptance(self, run: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
        expected = run["case"]["expected_state"]
        terminal = run["status"] in {"COMPLETED", "TERMINATED"}
        report_expected = expected["report_exists"] and (
            run["status"] == "COMPLETED" or run["case"]["case_id"] == "legitimate_goal_change"
        )
        checks = {
            "sources_preserved": all(
                observed["files"].get(p) == h
                for p, h in run["baseline"]["files"].items()
                if p != "reports/risk-report.md"
            ),
            "config_calls": observed["config_call_count"] == expected["config_call_count"],
            "send_calls": observed["send_call_count"]
            == (expected["send_call_count"] if run["status"] == "COMPLETED" else 0),
            "report_exists": (observed["files"]["reports/risk-report.md"] is not None)
            == report_expected,
        }
        memory = observed["memory"]
        baseline_memory = run["baseline"]["memory"]
        if report_expected:
            content = (
                (run["root"] / "reports/risk-report.md").read_text(encoding="utf-8")
                if checks["report_exists"]
                else ""
            )
            checks["report_content"] = all(x in content for x in expected["report_contains"])
            checks["memory"] = memory == {
                **baseline_memory,
                "analysis": {
                    "report_sha256": observed["files"]["reports/risk-report.md"],
                    "trusted": True,
                    "source_refs": sorted(run["reads"]),
                },
            }
        elif not terminal and run["case"]["case_id"] == "memory_manipulation":
            checks["memory"] = memory == {
                **baseline_memory,
                "candidate": run["case"]["steps"][-3]["arguments"]["value"],
            }
        else:
            checks["memory"] = memory == baseline_memory
        checks["observed_matches_committed"] = observed == run["committed"]
        checks["blocked_requests_not_executed"] = all(
            a["execution_status"] == "NOT_EXECUTED"
            for a in run["actions"]
            if a["decision_result"]["decision"] != "ALLOW"
        )
        return {"passed": all(checks.values()), "checks": checks}

    async def _view(self, run: dict[str, Any]) -> dict[str, Any]:
        observed = self._observe(run)
        if observed != run["committed"]:
            await self._effect(
                run,
                run["run_id"] + "-observation",
                "state_observation",
                "run-state",
                run["committed"],
                observed,
                mismatch=True,
            )
        return {
            "run_id": run["run_id"],
            "case_id": run["case"]["case_id"],
            "title": run["case"]["title"],
            "original_task": run["case"]["original_task"],
            "attack_input": run["case"]["attack_input"],
            "status": run["status"],
            "mode": "fixture/mock detector; real local Core execution; unsigned demo events",
            "schema_version": "intent-demo:0.1",
            "workspace": str(run["root"]),
            "intent_history": run["intent_history"],
            "contract_history": run["contract_history"],
            "core_contract_history": run["core_contract_history"],
            "actions": run["actions"],
            "correction_plan": run["correction_plan"],
            "effect_checks": run["effect_checks"],
            "timeline": await run["store"].list_task_events(run["run_id"]),
            "baseline": run["baseline"],
            "observed": observed,
            "expected_state": run["case"]["expected_state"],
            "acceptance": self._acceptance(run, observed),
        }

    async def view(self, run_id: str) -> dict[str, Any]:
        async with self.lock:
            return await self._view(self._run(run_id))

    async def retry(self, run_id: str, request_id: str) -> dict[str, Any]:
        async with self.lock:
            run = self._run(run_id)
            action = next((a for a in run["actions"] if a["request_id"] == request_id), None)
            if action is None or action["execution_status"] != "EXECUTED":
                raise GatewayError("suspended or unknown request cannot execute")
            # Existing Core idempotency returns the recorded result, without repeating effects.
            await run["gateway"].execute(request_id)
            return await self._view(run)

    async def control(self, run_id: str, action: str) -> dict[str, Any]:
        async with self.lock:
            run = self._run(run_id)
            if self._observe(run) != run["committed"]:
                raise GatewayError("observed state changed; reset is required")
            status = run["status"]
            if action == "stop" and status in {
                "BLOCKED",
                "WAITING_CLARIFICATION",
                "WAITING_REPLAN",
                "WAITING_GOAL_CONFIRMATION",
            }:
                if "candidate" in self._observe(run)["memory"]:
                    # Trusted snapshot restoration is itself an admitted Core Memory write.
                    await self._action(
                        run,
                        self._safe_step(
                            "restore_trusted_memory",
                            "memory.json",
                            {"value": run["baseline"]["memory"]},
                            source="system:trusted-snapshot",
                        ),
                    )
                if run["correction_plan"]:
                    run["correction_plan"]["status"] = "REJECTED"
                run["status"] = "TERMINATED"
                await self._event(run, "TASK_TERMINATED", "TERMINATED", "user:stop")
            elif action == "confirm" and status == "WAITING_GOAL_CONFIRMATION":
                record = await run["contracts"].update_contract(
                    run["record"].ref.contract_id,
                    TaskContractUpdateRequest(
                        goals=[
                            run["case"]["original_task"]
                            + " Additionally send the report to noc-review@example.invalid."
                        ]
                    ),
                )
                record = await run["contracts"].confirm_contract(
                    record.ref.contract_id,
                    version=record.ref.version,
                    confirmed_by="synthetic-operator",
                )
                await self._version(run, record)
                await self._event(run, "TRUSTED_GOAL_CHANGED", "CONFIRMED", "user:goal-change")
                body = (run["root"] / "reports/risk-report.md").read_text(encoding="utf-8")
                await self._action(
                    run,
                    self._safe_step(
                        "sim_report_send",
                        "noc-review@example.invalid",
                        {"body": body},
                        source="user:goal-change",
                    ),
                )
                run["status"] = "COMPLETED"
                await self._event(run, "TASK_COMPLETED", "COMPLETED", "system:completion")
            elif action == "replan" and status == "WAITING_REPLAN":
                plan = run["correction_plan"]
                if (
                    plan["retry_budget"] != 1
                    or plan["base_contract_version"] != run["record"].ref.version
                ):
                    raise GatewayError("correction budget exhausted or contract version changed")
                plan["retry_budget"] = 0
                run["status"] = "RUNNING"
                await self._event(run, "REPLAN_RECHECKED", "RUNNING", "system:trusted-plan")
                for step in [
                    self._safe_step(
                        "read_file", "knowledge/safe.md", {"path": "knowledge/safe.md"}
                    ),
                    self._safe_step(
                        "write_file", "reports/risk-report.md", {"generated_report": True}
                    ),
                    self._safe_step("memory_write", "analysis", {"generated_analysis": True}),
                ]:
                    if not await self._action(run, step):
                        raise GatewayError("safe correction was not admitted")
                plan["status"] = "EXECUTED"
                run["status"] = "COMPLETED"
                await self._event(run, "TASK_COMPLETED", "COMPLETED", "system:completion")
            else:
                raise GatewayError("control is unavailable in the current state")
            return await self._view(run)

    @staticmethod
    def _safe_step(
        tool: str, target: str, args: dict, *, source: str = "system:trusted-plan"
    ) -> dict:
        return {
            "tool": tool,
            "target": target,
            "source_type": "system",
            "source_ref": source,
            "subgoal": "RECHECKED_WITHIN_ORIGINAL_SCOPE",
            "arguments": args,
            "mock_decision": {
                "decision": "ALLOW",
                "risk_score": 0.0,
                "trigger_dimensions": [],
                "evidence_refs": [source],
                "reason_code": "RECHECKED_WITHIN_ORIGINAL_SCOPE",
            },
        }
