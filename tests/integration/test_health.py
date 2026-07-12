from fastapi.testclient import TestClient

from ra_agent.main import app


def test_health_endpoint_reports_phase_one_runtime_foundation() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "data": {"status": "ok", "phase": "phase-1-runtime-foundation"},
        "error": None,
    }
