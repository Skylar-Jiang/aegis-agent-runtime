import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ra_agent.audit import AuditExportService, FileCheckpointStore
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.events import SqliteEventStore
from ra_agent.main import create_app
from tests.integration.test_core_crypto_event_integration import _run_real_evidence_gateway
from tests.unit.events.test_sqlite_event_store import event


@pytest.mark.parametrize("limit", [None, 2])
def test_events_route_reports_verified_log_failure_without_server_error(tmp_path, limit):
    store = SqliteEventStore(tmp_path / "core.sqlite3")
    asyncio.run(store.append_event(event()))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE core_events SET chain_hash=?", ("f" * 64,))
    app = create_app(
        Settings(
            runtime_mode=RuntimeMode.OFFLINE,
            workspace_root=tmp_path / "workspace",
            core_event_log_path=tmp_path / "events.jsonl",
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )
    app.state.core_event_store = store
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/tasks/task/events", params={"limit": limit} if limit else None
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LOG_INVALID"


def test_events_route_reports_unavailable_store(tmp_path):
    store = SqliteEventStore(tmp_path / "core.sqlite3")
    store.path = tmp_path  # sqlite3 cannot open a directory as its database file.
    app = create_app(
        Settings(
            runtime_mode=RuntimeMode.OFFLINE,
            workspace_root=tmp_path / "workspace",
            core_event_log_path=tmp_path / "events.jsonl",
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )
    app.state.core_event_store = store
    with TestClient(app) as client:
        response = client.get("/api/v1/tasks/task/events")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "STORAGE_UNAVAILABLE"


def test_export_route_reports_corrupt_event_store_as_conflict(tmp_path):
    contract, source, evidence, envelopes, _ = asyncio.run(_run_real_evidence_gateway(tmp_path))
    store = SqliteEventStore(tmp_path / "core.sqlite3")
    for row in asyncio.run(source.list_task_events(contract.contract.task_id)):
        asyncio.run(store.append_event(row))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE core_events SET chain_hash=? WHERE sequence=1", ("f" * 64,))
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=FileCheckpointStore(tmp_path / "checkpoints", envelopes),
        key_id="export-key",
    )
    app = create_app(
        Settings(
            runtime_mode=RuntimeMode.OFFLINE,
            workspace_root=tmp_path / "workspace",
            core_event_log_path=tmp_path / "events.jsonl",
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )
    app.state.core_audit_exporter = exporter
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/audit/export",
            json={"task_id": contract.contract.task_id, "checkpoint_id": "corrupt-export"},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LOG_INVALID"
