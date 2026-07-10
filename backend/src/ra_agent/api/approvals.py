from fastapi import APIRouter

from ra_agent.contracts import APIResponse

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.post("/{approval_id}/grant")
async def grant(approval_id: str) -> APIResponse[dict[str, str]]:
    return APIResponse(data={"approval_id": approval_id, "status": "GRANTED"})


@router.post("/{approval_id}/deny")
async def deny(approval_id: str) -> APIResponse[dict[str, str]]:
    return APIResponse(data={"approval_id": approval_id, "status": "DENIED"})
