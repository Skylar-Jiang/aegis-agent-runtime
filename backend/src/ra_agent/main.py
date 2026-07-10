from fastapi import FastAPI

from ra_agent.api import approvals_router, reports_router, streams_router, tasks_router
from ra_agent.contracts import APIResponse

app = FastAPI(title="RA-Agent Runtime", version="0.1.0")
app.include_router(tasks_router)
app.include_router(approvals_router)
app.include_router(reports_router)
app.include_router(streams_router)


@app.get("/health")
async def health() -> APIResponse[dict[str, str]]:
    return APIResponse(data={"status": "ok", "phase": "phase-0"})
