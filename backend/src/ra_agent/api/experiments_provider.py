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
