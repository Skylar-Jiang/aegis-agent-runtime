import pytest

from ra_agent.contracts import StepStatus
from ra_agent.runtime.state_machine import InvalidStateTransition, transition


def test_low_risk_path_is_legal() -> None:
    current = StepStatus.PLANNED
    for target in (
        StepStatus.RISK_CLASSIFYING,
        StepStatus.READY,
        StepStatus.EXECUTING_FAST,
        StepStatus.COMMITTED,
    ):
        current = transition(current, target)

    assert current is StepStatus.COMMITTED


def test_execution_cannot_start_before_risk_classification() -> None:
    with pytest.raises(InvalidStateTransition):
        transition(StepStatus.PLANNED, StepStatus.EXECUTING_FAST)


def test_terminal_state_cannot_transition() -> None:
    with pytest.raises(InvalidStateTransition):
        transition(StepStatus.BLOCKED, StepStatus.READY)
