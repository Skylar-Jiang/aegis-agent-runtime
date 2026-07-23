"""Experiment API — run and retrieve offline experiment results."""

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ra_agent.contracts import APIResponse
from ra_agent.core.container import ServiceContainer

from .deps import get_services

router = APIRouter(prefix="/api/experiments", tags=["experiments"])

EXPERIMENTS_DIR = Path(__file__).parent.parent.parent.parent / "experiments"


def _results_dir() -> Path:
    return EXPERIMENTS_DIR / "results"


@router.get("/results")
async def list_results(
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[list[dict[str, Any]]]:
    rd = _results_dir()
    if not rd.exists():
        return APIResponse(data=[])
    files = sorted(rd.glob("*.json"), reverse=True)
    file_list = [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        }
        for f in files
    ]
    return APIResponse(data=file_list)


@router.get("/results/{filename}")
async def get_result(
    filename: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, Any]]:
    path = _results_dir() / filename
    if not path.exists() or not path.suffix == ".json":
        return APIResponse(data={"error": "not_found", "filename": filename})
    data = json.loads(path.read_text(encoding="utf-8"))
    return APIResponse(data={"filename": filename, "results": data})


@router.get("/cases")
async def list_cases(
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[list[dict[str, Any]]]:
    cases_path = EXPERIMENTS_DIR / "cases" / "task_cases.json"
    if not cases_path.exists():
        return APIResponse(data=[])
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    return APIResponse(data=cases)
