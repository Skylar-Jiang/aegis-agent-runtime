from ra_agent.contracts import StepStatus


class InvalidStateTransition(ValueError):
    pass


_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PLANNED: frozenset({StepStatus.RISK_CLASSIFYING, StepStatus.CANCELLED}),
    StepStatus.RISK_CLASSIFYING: frozenset(
        {
            StepStatus.WAITING_PERMISSION,
            StepStatus.READY,
            StepStatus.CHECKPOINT_CREATING,
            StepStatus.WAITING_APPROVAL,
            StepStatus.BLOCKED,
            StepStatus.FAILED,
        }
    ),
    StepStatus.WAITING_PERMISSION: frozenset(
        {StepStatus.READY, StepStatus.WAITING_APPROVAL, StepStatus.BLOCKED, StepStatus.CANCELLED}
    ),
    StepStatus.WAITING_APPROVAL: frozenset(
        {StepStatus.READY, StepStatus.BLOCKED, StepStatus.CANCELLED}
    ),
    StepStatus.READY: frozenset(
        {StepStatus.EXECUTING_FAST, StepStatus.CHECKPOINT_CREATING, StepStatus.CANCELLED}
    ),
    StepStatus.CHECKPOINT_CREATING: frozenset(
        {StepStatus.EXECUTING_SANDBOX, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.EXECUTING_FAST: frozenset(
        {StepStatus.COMMITTED, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.EXECUTING_SANDBOX: frozenset(
        {
            StepStatus.SAFETY_CHECKING,
            StepStatus.COMMITTING,
            StepStatus.ROLLING_BACK,
            StepStatus.FAILED,
        }
    ),
    StepStatus.SAFETY_CHECKING: frozenset(
        {StepStatus.COMMITTING, StepStatus.ROLLING_BACK, StepStatus.FAILED}
    ),
    StepStatus.COMMITTING: frozenset(
        {StepStatus.COMMITTED, StepStatus.ROLLING_BACK, StepStatus.FAILED}
    ),
    StepStatus.ROLLING_BACK: frozenset({StepStatus.ROLLED_BACK, StepStatus.FAILED}),
}


def transition(current: StepStatus, target: StepStatus) -> StepStatus:
    if target not in _TRANSITIONS.get(current, frozenset()):
        raise InvalidStateTransition(f"Illegal step transition: {current} -> {target}")
    return target
