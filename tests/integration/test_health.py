from fastapi.testclient import TestClient

from ra_agent.main import app


def test_health_endpoint_reports_runtime_container_mode() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "status": "ok",
            "phase": "phase-2-runtime-container",
            "mode": "offline",
        },
        "error": None,
    }
