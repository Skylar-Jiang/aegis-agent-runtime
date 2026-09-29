from fastapi.testclient import TestClient

from tests.integration.test_core_http_hardening import _app, _body


def test_core_events_and_report_are_visible_through_workbench_routes(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = _body(client)
        client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
        execution = client.post("/api/v1/tool-calls/request-http/execute")
        execution.raise_for_status()
        assert (
            execution.json()["data"]["result"]["runtime"]["commit_status"]
            == "COMMITTED"
        )
        core = client.get("/api/v1/tasks/task-http/events").json()["data"]
        history = client.get("/api/tasks/task-http/events").json()["data"]["events"]
        assert len(history) == len(core)
        assert history[-1]["sequence_number"] == core[-1]["sequence"]
        report = client.get("/api/tasks/task-http/report").json()["data"]
        assert report["total_events"] == len(core)
        assert report["status"] == "EXECUTED"
