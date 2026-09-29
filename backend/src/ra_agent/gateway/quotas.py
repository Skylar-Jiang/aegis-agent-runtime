"""Durable quota reservations committed in the request admission transaction."""

from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any

from ra_agent.contracts.core_v1 import EffectivePermission, TaskContractV2, ToolCallEnvelope

from .state import CoreStateTransaction

SUPPORTED_LIMITS = {"max_affected_objects", "max_bytes", "max_calls", "max_output_bytes"}
MUTATING_TOOLS = {"create_file", "write_file", "delete_file", "memory_write", "send_email_dry_run"}


class QuotaExceeded(ValueError):
    pass


def _encoded(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _cost(envelope: ToolCallEnvelope) -> tuple[str | None, int]:
    if envelope.tool not in MUTATING_TOOLS:
        return None, 0
    resource = envelope.resource
    domain = "memory" if envelope.tool == "memory_write" else "egress"
    if envelope.tool in {"create_file", "write_file", "delete_file"}:
        domain = "file"
        resource = os.path.normcase(resource).replace("\\", "/")
    size = 0
    if envelope.tool in {"create_file", "write_file"}:
        content = envelope.canonical_args.get("content")
        if isinstance(content, str):
            size = len(content.encode("utf-8"))
    elif envelope.tool == "memory_write":
        size = len(_encoded(envelope.canonical_args.get("value")))
    elif envelope.tool == "send_email_dry_run":
        size = len(_encoded(envelope.canonical_args))
    return f"{domain}:{resource}", size


def reserve_quotas(
    transaction: CoreStateTransaction,
    envelope: ToolCallEnvelope,
    contract: TaskContractV2,
    effective: EffectivePermission,
    task_limits: dict[str, Any] | None = None,
) -> None:
    bindings: list[tuple[Any, dict[str, Any]]] = [
        (["contract", contract.contract_id], dict(contract.limits))
    ]
    task_key = hashlib.sha256(_encoded(["task", envelope.session_id, envelope.task_id])).hexdigest()
    if task_limits is not None:
        bindings.append((["task", envelope.session_id, envelope.task_id], task_limits))
    for grant in effective.allowed:
        identity = grant.model_dump(mode="json", exclude={"limits", "expires_at"})
        bindings.append((["grant", identity], dict(grant.limits)))
    resource, size = _cost(envelope)
    merged: dict[str, dict[str, int | float]] = {}
    for binding, limits in bindings:
        key = hashlib.sha256(_encoded(binding)).hexdigest()
        target = merged.setdefault(key, {})
        for name, value in limits.items():
            if name not in SUPPORTED_LIMITS:
                raise QuotaExceeded(f"LIMIT_UNSUPPORTED: {name}")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise QuotaExceeded(f"LIMIT_INVALID: {name}")
            target[name] = min(target.get(name, value), value)
    identities = {hashlib.sha256(_encoded(binding)).hexdigest(): binding for binding, _ in bindings}
    for key, limits in merged.items():
        # A process can affect an unbounded number of resources: never pretend that
        # its cwd describes its complete effects when finite resource quotas apply.
        if envelope.tool == "run_shell" and any(
            name in limits for name in ("max_affected_objects", "max_bytes")
        ):
            raise QuotaExceeded(
                "LIMIT_UNMEASURABLE: process resource effects require an adapter budget"
            )
        usage = transaction.get("quotas", key)
        if usage is None:
            # Preserve already used capacity on upgrade or later policy tightening.
            # This indexed history scan occurs only when the task counter is absent.
            usage = {"calls": 0, "bytes": 0, "objects": []}
            previous_objects: set[str] = set()
            binding = identities[key]
            history = transaction.requests_for_task(envelope.task_id)
            if binding[0] == "grant" and binding[1].get("scope") != "TASK":
                history = transaction.all_requests()
            for request in history:
                previous = ToolCallEnvelope.model_validate(request["envelope"])
                if previous.request_id == envelope.request_id or request["execution_state"] not in {
                    "EXECUTED",
                    "UNKNOWN",
                    "CLAIMED",
                    "ADMITTED",
                }:
                    continue
                if request["execution_state"] == "CLAIMED" and request.get("claim_id"):
                    # Current claims precede admission and have consumed no quota.
                    # Older rows without ownership IDs did not distinguish that
                    # phase from post-admission execution: retain their conservative
                    # charge when migrating an unknown legacy outcome.
                    continue
                if key == task_key and previous.session_id != envelope.session_id:
                    continue
                if (
                    binding[0] == "contract"
                    and previous.contract_ref.contract_id != contract.contract_id
                ):
                    continue
                if binding[0] == "grant":
                    evaluation = request.get("evaluation") or {}
                    grants = evaluation.get("effective_permission", {}).get("allowed", [])
                    if not any(
                        {k: v for k, v in grant.items() if k not in {"limits", "expires_at"}}
                        == binding[1]
                        for grant in grants
                    ):
                        continue
                old_resource, old_size = _cost(previous)
                usage["calls"] += 1
                usage["bytes"] += old_size
                if old_resource is not None:
                    previous_objects.add(old_resource)
            usage["objects"] = sorted(previous_objects)
        if usage is None:
            usage = {"calls": 0, "bytes": 0, "objects": []}
        objects = set(usage["objects"])
        if resource is not None:
            objects.add(resource)
        projected = {
            "max_calls": usage["calls"] + 1,
            "max_bytes": usage["bytes"] + size,
            "max_affected_objects": len(objects),
        }
        for name, amount in projected.items():
            if name in limits and amount > limits[name]:
                raise QuotaExceeded(f"LIMIT_EXCEEDED: {name}")
        transaction.put(
            "quotas",
            key,
            {
                "calls": projected["max_calls"],
                "bytes": projected["max_bytes"],
                "objects": sorted(objects),
            },
        )


def check_output_limit(result: Any, effective: EffectivePermission) -> None:
    limit = effective.constraints.get("max_output_bytes")
    if isinstance(limit, (int, float)) and len(_encoded(result)) > limit:
        raise QuotaExceeded("LIMIT_EXCEEDED: max_output_bytes")
