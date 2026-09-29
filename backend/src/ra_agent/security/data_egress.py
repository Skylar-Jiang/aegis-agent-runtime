"""Lineage-aware checks for dry-run data egress."""

from ra_agent.contracts import DataLineage, ToolCallRequest


class DataEgressGuard:
    """Reject unapproved recipients before an egress dry run is recorded."""

    def check(self, request: ToolCallRequest) -> tuple[bool, str]:
        if request.tool_name != "send_email_dry_run":
            return True, "not an egress request"
        artifact = request.arguments.get("artifact")
        recipient = request.arguments.get("recipient")
        if not isinstance(artifact, dict) or not isinstance(recipient, str) or not recipient:
            return False, "egress artifact and recipient are required"
        try:
            lineage = DataLineage.model_validate(artifact)
        except ValueError:
            return False, "egress artifact lineage is invalid"
        if recipient not in lineage.allowed_recipients:
            return False, "recipient is not allowed by artifact lineage"
        if lineage.sensitivity in {"CONFIDENTIAL", "SECRET"} and not lineage.allowed_recipients:
            return False, "sensitive artifact has no approved recipients"
        return True, "recipient is allowed by artifact lineage"
