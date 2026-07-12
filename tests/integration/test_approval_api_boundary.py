from fastapi.testclient import TestClient

from ra_agent.main import app


def test_approval_routes_do_not_pretend_to_be_connected_to_runtime_service() -> None:
    client = TestClient(app)

    for action in ("grant", "deny"):
        response = client.post(f"/api/approvals/approval-1/{action}")
        assert response.status_code == 501
        assert (
            response.json()["detail"] == "Approval API integration is not implemented"
        )
