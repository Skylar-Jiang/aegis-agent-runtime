import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/tasks", tags=["streams"])


async def mock_event(task_id: str) -> AsyncIterator[str]:
    payload = json.dumps({"task_id": task_id, "event_type": "TASK_CREATED"})
    yield f"event: audit\ndata: {payload}\n\n"


@router.get("/{task_id}/stream")
async def stream(task_id: str) -> StreamingResponse:
    return StreamingResponse(mock_event(task_id), media_type="text/event-stream")
