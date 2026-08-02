"""Audit report generation — summary statistics from audit events."""

from typing import Any

_CONTROL_EVENTS = {
    "risk": {"RISK_CLASSIFIED"},
    "policy": {"PERMISSION_CHECKED", "TOOL_BLOCKED"},
    "approval": {"APPROVAL_REQUESTED", "APPROVAL_GRANTED", "APPROVAL_DENIED"},
    "checkpoint": {"CHECKPOINT_CREATED"},
    "effect": {"EXECUTION_STARTED", "EXECUTION_FINISHED", "EXECUTION_INTERRUPTED"},
    "commit": {"COMMIT_STARTED", "COMMIT_FINISHED"},
    "rollback": {"ROLLBACK_STARTED", "ROLLBACK_FINISHED"},
}


def generate_report(task_id: str, *, events: list[dict[str, Any]]) -> dict[str, Any]:
    """Produce a structured security report from a task's audit events.

    Args:
        task_id: The task to report on.
        events: Audit event dicts (from AuditRecorder.events_for or API query).

    Returns:
        A dict with task_id, total_events, status, event_summary, risk_summary,
        and timeline suitable for API / frontend consumption.
    """
    if not events:
        return {
            "task_id": task_id,
            "total_events": 0,
            "status": "no_events",
            "event_summary": {},
            "risk_summary": {},
            "control_summary": {
                "risk": 0,
                "policy": 0,
                "approval": 0,
                "checkpoint": 0,
                "effect": 0,
                "commit": 0,
                "rollback": 0,
                "audit": 0,
            },
            "timeline": [],
        }

    event_summary: dict[str, int] = {}
    statuses: dict[str, int] = {}
    risk_levels: dict[str, int] = {}
    control_summary = {name: 0 for name in _CONTROL_EVENTS}
    timeline: list[dict[str, Any]] = []

    for evt in events:
        et = str(evt.get("event_type", ""))
        event_summary[et] = event_summary.get(et, 0) + 1
        for control, event_types in _CONTROL_EVENTS.items():
            if et in event_types:
                control_summary[control] += 1

        st = str(evt.get("status", ""))
        statuses[st] = statuses.get(st, 0) + 1

        rl = evt.get("risk_level")
        if rl:
            risk_levels[str(rl)] = risk_levels.get(str(rl), 0) + 1

        timeline.append({
            "sequence_number": evt.get("sequence_number"),
            "event_type": evt.get("event_type"),
            "status": evt.get("status"),
            "summary": evt.get("summary"),
            "timestamp": evt.get("timestamp"),
        })

    # Determine overall task status from the last event
    last_status = str(events[-1].get("status", "unknown"))

    return {
        "task_id": task_id,
        "total_events": len(events),
        "status": last_status,
        "event_summary": event_summary,
        "risk_summary": risk_levels,
        "control_summary": {**control_summary, "audit": len(events)},
        "timeline": sorted(timeline, key=lambda e: e.get("sequence_number", 0)),
    }
