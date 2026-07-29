"""Experiment API provider — read-only access to experiment results and report data.

Member 4 (Visualization / Experiments / Dashboard) provides this module.
Registration into the FastAPI app is done by the team lead in main.py.
"""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from ra_agent.contracts import APIResponse

EXPERIMENTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "experiments"


def _raw_dir() -> Path:
    return EXPERIMENTS_DIR / "results" / "raw"


def _derived_dir() -> Path:
    return EXPERIMENTS_DIR / "results" / "derived"


router = APIRouter(prefix="/api/experiments", tags=["experiments"])


@router.get("/results")
async def list_results() -> APIResponse[list[dict[str, Any]]]:
    rd = _raw_dir()
    if not rd.exists():
        return APIResponse(data=[])
    files = sorted(rd.glob("*.json"), reverse=True)
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


@router.get("/results/{filename}")
async def get_result(filename: str) -> APIResponse[dict[str, Any]]:
    path = _raw_dir() / filename
    if not path.exists() or not path.suffix == ".json":
        return APIResponse(data={"error": "not_found", "filename": filename})
    data = json.loads(path.read_text(encoding="utf-8"))
    return APIResponse(data={"filename": filename, "results": data})


@router.get("/cases")
async def list_cases() -> APIResponse[list[dict[str, Any]]]:
    cases_path = EXPERIMENTS_DIR / "cases" / "task_cases.json"
    if not cases_path.exists():
        return APIResponse(data=[])
    return APIResponse(data=json.loads(cases_path.read_text(encoding="utf-8")))
