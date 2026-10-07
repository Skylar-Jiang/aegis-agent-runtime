import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

import anyio
from fastapi import FastAPI

from ra_agent.api import (
    approvals_router,
    conversation_router,
    core_v1_router,
    demo_router,
    experiments_router,
    graph_router,
    profile_router,
    reports_router,
    streams_router,
    task_graph_router,
    tasks_router,
    tools_router,
)
from ra_agent.api.body_limits import CoreBodyLimitMiddleware
from ra_agent.api.core_intent import router as core_intent_router
from ra_agent.api.core_views import router as core_views_router
from ra_agent.confirmations import ConfirmationService
from ra_agent.contracts import APIResponse, ContractService
from ra_agent.core.bootstrap import (
    build_agent_runner,
    build_runtime_container,
    build_task_graph_scheduler,
)
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.crypto.runtime import build_core_crypto_runtime
from ra_agent.database.migrate import upgrade_database
from ra_agent.database.task_store import InMemoryTaskStore, PersistentTaskStore
from ra_agent.database.workbench_store import (
    InMemoryConversationStore,
    InMemorySecurityProfileStore,
    PersistentConversationStore,
    PersistentSecurityProfileStore,
)
from ra_agent.gateway import ToolGateway
from ra_agent.gateway.admission import AdmissionGuard, GuardedSecurityProfileStore
from ra_agent.gateway.audit_view import CoreAuditView
from ra_agent.gateway.runtime_bridge import build_core_runtime_bridge
from ra_agent.gateway.state import CoreStateStore
from ra_agent.gateway.trusted_policy import TrustedCorePolicy
from ra_agent.permissions import PermissionResolver


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if runtime_settings.runtime_mode is not RuntimeMode.OFFLINE:
            await asyncio.to_thread(upgrade_database, runtime_settings.database_url)
        await app.state.task_store.mark_orphaned_running_tasks()
        await app.state.security_profile_store.ensure_default()
        try:
            yield
        finally:
            planner = getattr(app.state.agent_runner, "planner", None)
            close = getattr(planner, "aclose", None)
            if close is not None:
                await close()
            database_engine = app.state.services.database_engine
            if database_engine is not None:
                await database_engine.dispose()

    app = FastAPI(title="Aegis Runtime Base", version="0.4.0", lifespan=lifespan)
    app.add_middleware(CoreBodyLimitMiddleware, max_write_bytes=runtime_settings.max_write_bytes)
    app.state.core_audit_limiter = anyio.CapacityLimiter(2)
    app.state.runtime_settings = runtime_settings
    app.state.services = build_runtime_container(runtime_settings)
    engine = app.state.services.database_engine
    app.state.task_store = (
        PersistentTaskStore(engine) if engine is not None else InMemoryTaskStore()
    )
    app.state.security_profile_store = (
        PersistentSecurityProfileStore(engine)
        if engine is not None
        else InMemorySecurityProfileStore()
    )
    app.state.conversation_store = (
        PersistentConversationStore(engine) if engine is not None else InMemoryConversationStore()
    )
    app.state.enable_demo_fixtures = runtime_settings.enable_demo_fixtures
    # Core defaults to explicit fake mode for legacy development. SM2 mode constructs
    # the complete P2/P3 evidence stack and fails closed on missing trusted inputs.
    core_crypto = build_core_crypto_runtime(runtime_settings)
    core_state = CoreStateStore(
        runtime_settings.core_state_path
        or runtime_settings.core_event_log_path.with_suffix(".state.sqlite3")
    )
    app.state.core_state_store = core_state
    guard = AdmissionGuard(
        (core_state.path or runtime_settings.core_event_log_path).with_suffix(".admission.lock")
    )
    app.state.core_admission_guard = guard
    app.state.security_profile_store = GuardedSecurityProfileStore(
        app.state.security_profile_store, guard
    )
    app.state.core_contract_service = ContractService(store=core_state)
    app.state.core_permission_resolver = PermissionResolver()
    app.state.core_crypto_mode = core_crypto.mode
    app.state.core_event_store = core_crypto.event_store
    app.state.core_signature_provider = core_crypto.signature_provider
    app.state.core_evidence_recorder = core_crypto.evidence_recorder
    app.state.core_audit_exporter = core_crypto.audit_exporter
    app.state.core_audit_verifier = core_crypto.audit_verifier
    app.state.core_confirmation_service = ConfirmationService(store=core_state)
    bridge = build_core_runtime_bridge(runtime_settings, app.state.services)
    app.state.services = bridge.services
    app.state.core_tool_executor = bridge
    app.state.core_policy = TrustedCorePolicy(
        app, runtime_settings.security_config_dir / "core_skills.json"
    )
    from ra_agent.security.intent import IntentRuleGuard
    from ra_agent.security.intent_policy import load_policy

    intent_policy_path = (
        core_state.path or runtime_settings.core_event_log_path
    ).parent / "intent-policy.json"
    app.state.core_gateway = ToolGateway(
        contracts=app.state.core_contract_service,
        resolver=app.state.core_permission_resolver,
        event_store=core_crypto.event_store,
        signature_provider=core_crypto.signature_provider,
        evidence_recorder=core_crypto.evidence_recorder,
        confirmation_service=app.state.core_confirmation_service,
        executor=app.state.core_tool_executor,
        store=core_state,
        request_validator=app.state.core_policy.validate_envelope,
        permission_provider=app.state.core_policy.authority_for,
        admission_guard=guard,
        task_limit_provider=app.state.core_policy.task_limits_for,
        intent_guard=IntentRuleGuard(
            core_state,
            policy_provider=lambda: load_policy(intent_policy_path),
        ),
    )
    app.state.services = replace(
        app.state.services,
        audit_recorder=CoreAuditView(app.state.services.audit_recorder, core_crypto.event_store),
    )
    app.state.core_session_users = {}
    app.state.agent_runner = build_agent_runner(runtime_settings, app.state.services)
    app.state.task_graph_scheduler = build_task_graph_scheduler(
        app.state.services,
        runtime_scheduler=app.state.agent_runner.scheduler,
    )
    app.include_router(tasks_router)
    app.include_router(core_v1_router)
    app.include_router(core_views_router)
    app.include_router(core_intent_router)
    app.include_router(profile_router)
    app.include_router(conversation_router)
    app.include_router(tools_router)
    app.include_router(task_graph_router)
    app.include_router(graph_router)
    app.include_router(approvals_router)
    app.include_router(demo_router)
    app.include_router(reports_router)
    app.include_router(streams_router)
    app.include_router(experiments_router)

    @app.get("/health")
    async def health() -> APIResponse[dict[str, str]]:
        return APIResponse(
            data={
                "status": "ok",
                "phase": "runtime-base-main-chain",
                "mode": runtime_settings.runtime_mode.value,
            }
        )

    return app


app = create_app()
