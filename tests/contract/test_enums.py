from ra_agent.contracts import AuditEventType, PolicyDecision, RiskLevel


def test_risk_levels_are_frozen() -> None:
    assert [level.value for level in RiskLevel] == [
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
        "FORBIDDEN",
    ]


def test_policy_decisions_are_frozen() -> None:
    assert [decision.value for decision in PolicyDecision] == [
        "FAST_EXECUTE",
        "SANDBOX_CHECK",
        "REQUEST_APPROVAL",
        "BLOCK",
    ]


def test_phase3_pre_and_post_check_audit_events_are_frozen() -> None:
    assert [
        AuditEventType.PRE_CHECK_STARTED.value,
        AuditEventType.PRE_CHECK_FINISHED.value,
        AuditEventType.POST_CHECK_STARTED.value,
        AuditEventType.POST_CHECK_FINISHED.value,
    ] == [
        "PRE_CHECK_STARTED",
        "PRE_CHECK_FINISHED",
        "POST_CHECK_STARTED",
        "POST_CHECK_FINISHED",
    ]
