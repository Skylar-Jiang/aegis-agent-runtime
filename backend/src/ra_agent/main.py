import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ra_agent.api import approvals_router, reports_router, streams_router, tasks_router
from ra_agent.contracts import APIResponse
from ra_agent.core.bootstrap import build_agent_runner, build_runtime_container
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.database.migrate import upgrade_database


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if runtime_settings.runtime_mode is not RuntimeMode.OFFLINE:
            await asyncio.to_thread(upgrade_database, runtime_settings.database_url)
        try:
            yield
        finally:
            close = getattr(app.state.agent_runner.planner, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(title="RA-Agent Runtime", version="0.2.0", lifespan=lifespan)
    app.state.services = build_runtime_container(runtime_settings)
    app.state.agent_runner = build_agent_runner(runtime_settings, app.state.services)
    app.include_router(tasks_router)
    app.include_router(approvals_router)
    app.include_router(reports_router)
    app.include_router(streams_router)

    @app.get("/health")
    async def health() -> APIResponse[dict[str, str]]:
        return APIResponse(
            data={
                "status": "ok",
                "phase": "phase-2-runtime-container",
                "mode": runtime_settings.runtime_mode.value,
            }
        )

    return app


app = create_app()
