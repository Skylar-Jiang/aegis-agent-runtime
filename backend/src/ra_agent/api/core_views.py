"""Additive read views for resuming Core tasks and inspecting recorded experiments."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ra_agent.contracts import APIResponse
from ra_agent.contracts.core_v1 import ContractRecord, SessionTaskDraftV1
from ra_agent.gateway.state import CoreStateTransaction

router = APIRouter(prefix="/api/v1", tags=["aegis-core-views"])
_EVIDENCE_ROOT = Path(__file__).resolve().parents[4] / "docs/evidence/core-completion"
_EXPERIMENTS = {
    "benchmark": "密码签名、验签与网关性能",
    "memory-boundary": "内存与输入边界",
    "boundary": "资源边界验证",
    "ablations": "协议机制消融对照",
    "http-controls": "真实 HTTP 控制链路",
}
_MAX_RESULT_BYTES = 2 * 1024 * 1024


def _recorded_at(data: dict[str, Any], path: Path) -> str:
    for field in ("generated_at", "finished_at", "created_at", "started_at"):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            return value
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()


@router.get("/tasks/{task_id}/snapshot")
async def task_snapshot(task_id: str, request: Request) -> APIResponse[dict[str, Any]]:
    # This is a read-only restore. It cannot admit a request or replay a side effect.
    async with request.app.state.core_admission_guard():
        legacy = await request.app.state.task_store.get(task_id)

        def read(tx: CoreStateTransaction) -> dict[str, Any]:
            cid = tx.get("task_contracts", task_id)
            if cid is None and legacy is not None:
                cid = (legacy.get("contract") or {}).get("contract_id")
            versions = tx.get("contracts", cid) if cid else None
            if not versions:
                raise HTTPException(status_code=404, detail="Unknown Core task")
            record = ContractRecord.model_validate(versions[-1])
            if record.contract.task_id != task_id:
                raise HTTPException(status_code=409, detail="Task contract binding is inconsistent")
            lifecycle = tx.get("task_lifecycle", task_id) or {}
            status = lifecycle.get(
                "status", "DRAFT" if record.ref.status.value == "DRAFT" else "READY"
            )
            # A newer unconfirmed version must be presented for review, not silently
            # replaced by a previously approved contract stored in the task listing.
            if record.ref.status.value == "DRAFT" and status != "CANCELLED":
                status = "DRAFT"
            rid = tx.get("task_latest_request", task_id)
            latest = tx.get("requests", rid) if rid else None
            if latest is not None and latest["envelope"]["task_id"] != task_id:
                raise HTTPException(status_code=409, detail="Request task binding is inconsistent")
            if latest is not None and any(
                latest["envelope"]["contract_ref"][field] != getattr(record.ref, field)
                for field in ("contract_id", "version", "digest")
            ):
                # The previous request is historical, not a request for this draft.
                latest = None
            return {
                "task": SessionTaskDraftV1(
                    task_id=task_id,
                    session_id=record.contract.session_id,
                    status=status,
                    contract=record,
                ).model_dump(mode="json"),
                "latest_request": {
                    field: latest.get(field)
                    for field in (
                        "envelope",
                        "evaluation",
                        "execution_state",
                        "execution_result",
                        "confirmation_id",
                    )
                }
                if latest is not None
                else None,
            }

        data = await request.app.state.core_state_store.run(read, readonly=True)
    return APIResponse(data=data)


def _recorded_experiments() -> dict[str, Any]:
    runs = []
    unavailable = []
    for run_id, title in _EXPERIMENTS.items():
        path = _EVIDENCE_ROOT / f"{run_id}.json"
        try:
            with path.open("rb") as stream:
                raw = stream.read(_MAX_RESULT_BYTES + 1)
            if len(raw) > _MAX_RESULT_BYTES:
                raise ValueError("Recorded result exceeds the read budget")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Recorded result must be an object")
            runs.append(
                {
                    "id": run_id,
                    "title": title,
                    "source": f"docs/evidence/core-completion/{path.name}",
                    "recorded_at": _recorded_at(data, path),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "data": data,
                }
            )
        except (OSError, ValueError) as error:
            unavailable.append({"id": run_id, "error": type(error).__name__})
    return {"recorded": True, "runs": runs, "unavailable": unavailable}


@router.get("/experiments/core")
async def core_experiments() -> APIResponse[dict[str, Any]]:
    return APIResponse(data=await asyncio.to_thread(_recorded_experiments))
