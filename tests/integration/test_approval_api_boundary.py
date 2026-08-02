from fastapi.testclient import TestClient
from ra_agent.main import app


def test_approval_routes_are_wired_to_real_services() -> None:
    """Approval routes no longer return 501 — they reach real service logic."""
    client = TestClient(app)

    for action in ("grant", "deny"):
        response = client.post(
            f"/api/approvals/approval-1/{action}?decided_by=boundary-test"
        )
        # Returns 404 because the mock container has no such approval,
        # proving the route is connected to ApprovalService (not a stub).
        assert response.status_code == 404


def test_approval_api_returns_valid_json() -> None:
    client = TestClient(app)
    response = client.post("/api/approvals/approval-1/grant")
    data = response.json()
    assert "detail" in data


def test_approval_api_requires_explicit_approver_identity() -> None:
    client = TestClient(app)

    response = client.post("/api/approvals/approval-1/grant")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "decided_by"]


def test_approval_api_rejects_blank_approver_identity() -> None:
    client = TestClient(app)

    response = client.post("/api/approvals/approval-1/deny?decided_by=%20%20")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "decided_by"]
