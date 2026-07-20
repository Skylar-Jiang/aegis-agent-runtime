from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ra_agent.contracts import APIResponse
from ra_agent.core.container import ServiceContainer

from .deps import get_services

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

# Phase 4 production integration must supply this trusted caller identity from auth.


@router.post("/{approval_id}/grant")
async def grant(
    approval_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
    decided_by: Annotated[str, Query(min_length=1, pattern=r".*\S.*")],
    reason: str = "approved",
) -> APIResponse[dict[str, str]]:
    try:
        decision = await services.approval_service.grant(
            approval_id, decided_by, reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(
        data={
            "approval_id": decision.approval_id,
            "status": decision.status.value,
            "decided_by": decision.decided_by,
            "reason": decision.reason,
        }
    )


@router.post("/{approval_id}/deny")
async def deny(
    approval_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
    decided_by: Annotated[str, Query(min_length=1, pattern=r".*\S.*")],
    reason: str = "denied",
) -> APIResponse[dict[str, str]]:
    try:
        decision = await services.approval_service.deny(
            approval_id, decided_by, reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(
        data={
            "approval_id": decision.approval_id,
            "status": decision.status.value,
            "decided_by": decision.decided_by,
            "reason": decision.reason,
        }
    )
