from datetime import UTC, datetime

import asyncio

from ra_agent.contracts import SourceType, TaskContract, ToolCallRequest
from ra_agent.security.data_egress import DataEgressGuard
from ra_agent.tools.implementations.egress import SendEmailDryRunHandler


def _request(recipient: str) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-egress",
        step_id="step-egress",
        request_id="request-egress",
        tool_name="send_email_dry_run",
        arguments={
            "recipient": recipient,
            "artifact": {
                "artifact_id": "report-1",
                "owner": "finance",
                "sensitivity": "SECRET",
                "source": "workspace/report.csv",
                "allowed_recipients": ["finance@example.com"],
            },
        },
        objective="Prepare an approved dry-run email",
        context_summary="egress test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
        task_contract=TaskContract(
            allowed_actions=["send_email_dry_run"],
            allowed_resources=["*"],
            max_affected_objects=1,
            allow_egress=True,
        ),
    )


def test_data_egress_guard_reblocks_sensitive_artifact_for_new_recipient() -> None:
    allowed, reason = DataEgressGuard().check(_request("attacker@example.com"))

    assert not allowed
    assert "not allowed" in reason


def test_dry_run_creates_pending_egress_without_sending() -> None:
    result = asyncio.run(SendEmailDryRunHandler()(_request("finance@example.com")))

    assert result.output["status"] == "PENDING_EGRESS"
    assert result.artifacts[0]["artifact_type"] == "tool_output"
