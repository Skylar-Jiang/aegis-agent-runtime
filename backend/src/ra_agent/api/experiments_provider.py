"""Experiment API provider — read-only access to experiment results and report data.

Member 4 (Visualization / Experiments / Dashboard) provides this module.
Registration into the FastAPI app is done by the team lead in main.py.
"""

import csv
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ra_agent.contracts import APIResponse

EXPERIMENTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "experiments"
_RESULT_SUFFIXES = frozenset({".json", ".jsonl", ".csv"})


def _raw_dir() -> Path:
    return EXPERIMENTS_DIR / "v2" / "results" / "raw"


def _derived_dir() -> Path:
    return EXPERIMENTS_DIR / "v2" / "results" / "derived"


def _final_evidence_dir() -> Path:
    return EXPERIMENTS_DIR / "v2" / "results" / "final-evidence"


router = APIRouter(prefix="/api/experiments", tags=["experiments"])


def _result_path(filename: str) -> Path | None:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix not in _RESULT_SUFFIXES:
        return None
    path = _raw_dir() / candidate.name
    return path if path.is_file() else None


def _read_result_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else [payload]
    if path.suffix == ".jsonl":
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


async def final_evidence_summary() -> APIResponse[dict[str, Any]]:
    """Compute the presentation summary from frozen Final raw/derived evidence."""

    root = _final_evidence_dir()
    agent = _read_result_records(root / "real-agent-repeat" / "raw" / "v6-real-agent.jsonl")
    workflow = _read_result_records(root / "raw" / "final-workflow.jsonl")
    matrix = _read_result_records(root / "derived" / "final-three-mode-metric-matrix.csv")
    unsafe = [row for row in agent if row.get("category") == "unsafe"]
    proposals = [row for row in unsafe if int(row.get("unsafe_proposal_count", 0)) > 0]
    metrics = {row["metric"]: row for row in matrix}
    return APIResponse(
        data={
            "runtime_boundary": {
                "contained_proposals": sum(
                    row.get("containment") == "CONTAINED" for row in proposals
                ),
                "proposals": len(proposals),
                "unsafe_runs": len(unsafe),
                "unsafe_effects": sum(bool(row.get("actual_unsafe_side_effect")) for row in unsafe),
            },
            "scheduling": {
                "approval_only": round(
                    sum(
                        float(row["approval_actions"])
                        for row in workflow
                        if row["mode"] == "FULL_GUARD"
                    )
                ),
                "adaptive_runtime": round(
                    sum(
                        float(row["approval_actions"])
                        for row in workflow
                        if row["mode"] == "ADAPTIVE_RUNTIME"
                    )
                ),
                "dependency_conflict_violations": round(
                    sum(
                        float(row["dependency_violation_count"])
                        + float(row["conflict_violation_count"])
                        for row in workflow
                        if row["mode"] == "ADAPTIVE_RUNTIME"
                    )
                ),
            },
            "recovery": {
                "approval_only_scope": int(float(metrics["rollback_scope"]["Approval-only"])),
                "aegis_scope": int(float(metrics["rollback_scope"]["Aegis Runtime"])),
                "preservation_rate": float(metrics["preservation_rate"]["Aegis Runtime"]),
                "preservation_precision": float(metrics["recovery_precision"]["Aegis Runtime"]),
            },
        }
    )


@router.get("/results")
async def list_results() -> APIResponse[list[dict[str, Any]]]:
    rd = _raw_dir()
    if not rd.exists():
        return APIResponse(data=[])
    files = sorted(
        (path for path in rd.iterdir() if path.is_file() and path.suffix in _RESULT_SUFFIXES),
        reverse=True,
    )
    return APIResponse(
        data=[
            {
                "name": f.name,
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            }
            for f in files
        ]
    )


@router.get("/final-evidence")
async def get_final_evidence_summary() -> APIResponse[dict[str, Any]]:
    return await final_evidence_summary()


@router.get("/results/{filename}")
async def get_result(filename: str) -> APIResponse[dict[str, Any]]:
    path = _result_path(filename)
    if path is None:
        return APIResponse(data={"error": "not_found", "filename": filename})
    try:
        records = _read_result_records(path)
    except (csv.Error, json.JSONDecodeError, UnicodeDecodeError):
        return APIResponse(data={"error": "invalid_result", "filename": filename})
    return APIResponse(data={"filename": filename, "results": records})


@router.get("/cases")
async def list_cases() -> APIResponse[list[dict[str, Any]]]:
    cases_path = EXPERIMENTS_DIR / "cases" / "task_cases.json"
    if not cases_path.exists():
        return APIResponse(data=[])
    return APIResponse(data=json.loads(cases_path.read_text(encoding="utf-8")))
