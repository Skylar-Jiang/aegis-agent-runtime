"""Opt-in, single-process, local synthetic telecom demonstration API."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from ra_agent.contracts.common import APIResponse, ContractModel
from ra_agent.gateway import GatewayError
from ra_agent.intent_demo.service import TelecomDemoService

router = APIRouter(prefix="/api/intent-demo", tags=["intent-demo"])


class StartCase(ContractModel):
    case_id: str


class ControlCase(ContractModel):
    action: Literal["confirm", "replan", "stop"]


def service(request: Request) -> TelecomDemoService:
    if not request.app.state.enable_demo_fixtures:
        raise HTTPException(status_code=404, detail="Synthetic demo fixtures are disabled")
    if not hasattr(request.app.state, "telecom_demo"):
        request.app.state.telecom_demo = TelecomDemoService(
            request.app.state.runtime_settings.workspace_root
        )
    return request.app.state.telecom_demo


@router.get("/cases")
async def cases(request: Request) -> APIResponse[list[dict]]:
    return APIResponse(data=service(request).list_cases())


@router.post("/runs")
async def start(request: Request, body: StartCase) -> APIResponse[dict]:
    try:
        return APIResponse(data=await service(request).start(body.case_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown synthetic Case") from exc


@router.get("/runs/{run_id}")
async def view(request: Request, run_id: str) -> APIResponse[dict]:
    try:
        return APIResponse(data=await service(request).view(run_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run missing or reset") from exc


@router.post("/runs/{run_id}/control")
async def control(request: Request, run_id: str, body: ControlCase) -> APIResponse[dict]:
    try:
        return APIResponse(data=await service(request).control(run_id, body.action))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run missing or reset") from exc
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/runs/{run_id}/actions/{request_id}/execute")
async def retry(request: Request, run_id: str, request_id: str) -> APIResponse[dict]:
    try:
        return APIResponse(data=await service(request).retry(run_id, request_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Run missing or reset") from exc
    except GatewayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/reset")
async def reset(request: Request) -> APIResponse[dict]:
    try:
        return APIResponse(data=await service(request).reset())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
