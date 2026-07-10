from datetime import UTC, datetime

from fastapi import APIRouter

from ra_agent.contracts import APIResponse, TaskCreateRequest, TaskResponse

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("")
async def create_task(request: TaskCreateRequest) -> APIResponse[TaskResponse]:
    return APIResponse(
        data=TaskResponse(
            task_id="mock-task",
            objective=request.objective,
            status="PLANNED",
            created_at=datetime.now(UTC),
        )
    )


@router.get("/{task_id}")
async def get_task(task_id: str) -> APIResponse[dict[str, str]]:
    return APIResponse(data={"task_id": task_id, "status": "MOCK"})


@router.get("/{task_id}/steps")
async def get_steps(task_id: str) -> APIResponse[dict[str, object]]:
    return APIResponse(data={"task_id": task_id, "steps": []})


@router.get("/{task_id}/events")
async def get_events(task_id: str) -> APIResponse[dict[str, object]]:
    return APIResponse(data={"task_id": task_id, "events": []})


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str) -> APIResponse[dict[str, str]]:
    return APIResponse(data={"task_id": task_id, "status": "CANCELLED"})
