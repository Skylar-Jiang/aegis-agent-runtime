from ra_agent.audit.report_generator import generate_report


def test_generate_report_empty_events() -> None:
    report = generate_report("task-1", events=[])
    assert report["task_id"] == "task-1"
    assert report["total_events"] == 0
    assert report["status"] == "no_events"
    assert report["event_summary"] == {}
    assert report["risk_summary"] == {}
    assert report["timeline"] == []


def test_generate_report_with_events() -> None:
    events = [
        {
            "sequence_number": 1,
            "event_type": "TASK_CREATED",
            "status": "CREATED",
            "summary": "Task created",
            "timestamp": "2026-01-01T00:00:00Z",
            "risk_level": None,
        },
        {
            "sequence_number": 2,
            "event_type": "EXECUTION_STARTED",
            "status": "EXECUTING",
            "summary": "Started execution",
            "timestamp": "2026-01-01T00:00:01Z",
            "risk_level": "LOW",
        },
        {
            "sequence_number": 3,
            "event_type": "EXECUTION_FINISHED",
            "status": "SUCCESS",
            "summary": "Execution complete",
            "timestamp": "2026-01-01T00:00:02Z",
            "risk_level": "LOW",
        },
    ]
    report = generate_report("task-1", events=events)

    assert report["task_id"] == "task-1"
    assert report["total_events"] == 3
    assert report["status"] == "SUCCESS"
    assert report["event_summary"]["TASK_CREATED"] == 1
    assert report["event_summary"]["EXECUTION_STARTED"] == 1
    assert report["event_summary"]["EXECUTION_FINISHED"] == 1
    assert report["risk_summary"]["LOW"] == 2
    assert len(report["timeline"]) == 3


def test_generate_report_aggregates_risk_levels() -> None:
    events = [
        {"sequence_number": 1, "event_type": "RISK_CLASSIFIED", "status": "CLASSIFIED",
         "summary": "a", "timestamp": "", "risk_level": "LOW"},
        {"sequence_number": 2, "event_type": "RISK_CLASSIFIED", "status": "CLASSIFIED",
         "summary": "b", "timestamp": "", "risk_level": "HIGH"},
        {"sequence_number": 3, "event_type": "RISK_CLASSIFIED", "status": "CLASSIFIED",
         "summary": "c", "timestamp": "", "risk_level": "LOW"},
    ]
    report = generate_report("task-1", events=events)
    assert report["risk_summary"] == {"LOW": 2, "HIGH": 1}
