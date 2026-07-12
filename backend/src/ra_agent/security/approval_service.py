import asyncio
from datetime import UTC, datetime
from typing import Protocol

from ra_agent.contracts import ApprovalDecision, ApprovalRequest, ApprovalStatus


class ApprovalService(Protocol):
    async def create(self, approval: ApprovalRequest) -> ApprovalRequest: ...

    async def get_request(self, approval_id: str) -> ApprovalRequest: ...

    async def get_decision(self, approval_id: str) -> ApprovalDecision | None: ...

    async def grant(
        self, approval_id: str, decided_by: str, reason: str, *, decided_at: datetime | None = None
    ) -> ApprovalDecision: ...

    async def deny(
        self, approval_id: str, decided_by: str, reason: str, *, decided_at: datetime | None = None
    ) -> ApprovalDecision: ...

    async def expire(
        self, approval_id: str, *, decided_at: datetime | None = None
    ) -> ApprovalDecision: ...

    async def consume(self, approval_id: str) -> ApprovalDecision: ...


class MockApprovalService:
    """In-memory approval mock; it is not an API or durable approval store."""

    def __init__(self) -> None:
        self._requests: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, ApprovalDecision] = {}
        self._consumed: set[str] = set()
        self._lock = asyncio.Lock()

    async def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        async with self._lock:
            if approval.approval_id in self._requests:
                raise ValueError(f"Approval already exists: {approval.approval_id}")
            self._requests[approval.approval_id] = approval
            return approval

    async def get_request(self, approval_id: str) -> ApprovalRequest:
        async with self._lock:
            try:
                return self._requests[approval_id]
            except KeyError as error:
                raise KeyError(f"Unknown approval: {approval_id}") from error

    async def get_decision(self, approval_id: str) -> ApprovalDecision | None:
        async with self._lock:
            approval = self._requests.get(approval_id)
            if approval is None:
                raise KeyError(f"Unknown approval: {approval_id}")
            self._expire_if_overdue(approval)
            return self._decisions.get(approval_id)

    async def grant(
        self,
        approval_id: str,
        decided_by: str,
        reason: str,
        *,
        decided_at: datetime | None = None,
    ) -> ApprovalDecision:
        return await self._decide(
            approval_id,
            ApprovalStatus.GRANTED,
            decided_by,
            reason,
            decided_at or datetime.now(UTC),
        )

    async def deny(
        self,
        approval_id: str,
        decided_by: str,
        reason: str,
        *,
        decided_at: datetime | None = None,
    ) -> ApprovalDecision:
        return await self._decide(
            approval_id,
            ApprovalStatus.DENIED,
            decided_by,
            reason,
            decided_at or datetime.now(UTC),
        )

    async def expire(
        self, approval_id: str, *, decided_at: datetime | None = None
    ) -> ApprovalDecision:
        return await self._decide(
            approval_id,
            ApprovalStatus.EXPIRED,
            "system",
            "approval expired",
            decided_at or datetime.now(UTC),
        )

    async def _decide(
        self,
        approval_id: str,
        status: ApprovalStatus,
        decided_by: str,
        reason: str,
        decided_at: datetime,
    ) -> ApprovalDecision:
        async with self._lock:
            try:
                approval = self._requests[approval_id]
            except KeyError as error:
                raise KeyError(f"Unknown approval: {approval_id}") from error
            self._expire_if_overdue(approval)
            if approval_id in self._decisions:
                raise ValueError(f"Approval already decided: {approval_id}")
            decision = ApprovalDecision(
                approval_id=approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                request_id=approval.request_id,
                status=status,
                decided_by=decided_by,
                decided_at=decided_at,
                reason=reason,
            )
            self._decisions[approval_id] = decision
            return decision

    async def consume(self, approval_id: str) -> ApprovalDecision:
        async with self._lock:
            if approval_id in self._consumed:
                raise ValueError(f"Approval already consumed: {approval_id}")
            approval = self._requests.get(approval_id)
            if approval is None:
                raise KeyError(f"Unknown approval: {approval_id}")
            self._expire_if_overdue(approval)
            decision = self._decisions.get(approval_id)
            if decision is None:
                raise ValueError(f"Approval is still pending: {approval_id}")
            self._consumed.add(approval_id)
            return decision

    def _expire_if_overdue(self, approval: ApprovalRequest) -> None:
        if approval.approval_id not in self._decisions and datetime.now(UTC) >= approval.expires_at:
            self._decisions[approval.approval_id] = ApprovalDecision(
                approval_id=approval.approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                request_id=approval.request_id,
                status=ApprovalStatus.EXPIRED,
                decided_by="system",
                decided_at=datetime.now(UTC),
                reason="approval expired",
            )
