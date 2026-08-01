from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ra_agent.contracts import APIResponse, ApprovalDecision, ApprovalStatus
from ra_agent.core.container import ServiceContainer

from .deps import get_services
from .task_graphs import is_known_graph_task, list_approval_views

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

# Phase 4 production integration must supply this trusted caller identity from auth.


@router.get("")
async def list_approvals(
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
    status: ApprovalStatus | None = None,
    task_id: str | None = None,
) -> APIResponse[list[dict[str, str]]]:
    if task_id is not None and not is_known_graph_task(request, task_id) and task_id not in getattr(
        request.app.state, "task_runs", {}
    ):
        raise HTTPException(status_code=404, detail="Unknown task")
    return APIResponse(data=await list_approval_views(services, task_id=task_id, status=status))


@router.post("/{approval_id}/grant")
async def grant(
    approval_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
    decided_by: Annotated[str, Query(min_length=1, pattern=r".*\S.*")],
    reason: str = "approved",
) -> APIResponse[ApprovalDecision]:
    try:
        decision = await services.approval_service.grant(
            approval_id, decided_by, reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=decision)


@router.post("/{approval_id}/deny")
async def deny(
    approval_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
    decided_by: Annotated[str, Query(min_length=1, pattern=r".*\S.*")],
    reason: str = "denied",
) -> APIResponse[ApprovalDecision]:
    try:
        decision = await services.approval_service.deny(
            approval_id, decided_by, reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=decision)
