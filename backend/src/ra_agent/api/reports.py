from fastapi import APIRouter

from ra_agent.contracts import APIResponse

router = APIRouter(prefix="/api/tasks", tags=["reports"])


@router.get("/{task_id}/report")
async def report(task_id: str) -> APIResponse[dict[str, str]]:
    return APIResponse(data={"task_id": task_id, "status": "MOCK"})
