from fastapi import FastAPI

from ra_agent.api import approvals_router, reports_router, streams_router, tasks_router
from ra_agent.contracts import APIResponse
from ra_agent.core.bootstrap import build_mock_container

app = FastAPI(title="RA-Agent Runtime", version="0.2.0")
app.state.services = build_mock_container()
app.include_router(tasks_router)
app.include_router(approvals_router)
app.include_router(reports_router)
app.include_router(streams_router)


@app.get("/health")
async def health() -> APIResponse[dict[str, str]]:
    return APIResponse(data={"status": "ok", "phase": "phase-1-runtime-foundation"})
