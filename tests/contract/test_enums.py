from ra_agent.contracts import PolicyDecision, RiskLevel


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
