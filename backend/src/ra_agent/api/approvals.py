from fastapi import APIRouter, HTTPException

from ra_agent.contracts import APIResponse

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.post("/{approval_id}/grant")
async def grant(approval_id: str) -> APIResponse[dict[str, str]]:
    raise HTTPException(status_code=501, detail="Approval API integration is not implemented")


@router.post("/{approval_id}/deny")
async def deny(approval_id: str) -> APIResponse[dict[str, str]]:
    raise HTTPException(status_code=501, detail="Approval API integration is not implemented")
