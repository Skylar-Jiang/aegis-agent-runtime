from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from ra_agent.contracts import ApprovalDecision, ApprovalRequest, ApprovalStatus

if TYPE_CHECKING:
    from ra_agent.database.models import ApprovalRequestRow
    from ra_agent.database.repositories.approval import SqliteApprovalRepository


class ApprovalService(Protocol):
    async def create(self, approval: ApprovalRequest) -> ApprovalRequest: ...

    async def list_for_task(self, task_id: str) -> list[ApprovalRequest]: ...

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

    async def list_for_task(self, task_id: str) -> list[ApprovalRequest]:
        async with self._lock:
            return [approval for approval in self._requests.values() if approval.task_id == task_id]

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
        if status in {ApprovalStatus.GRANTED, ApprovalStatus.DENIED} and not decided_by.strip():
            raise ValueError("Approval decisions require a nonblank approver identity")
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


class PersistentApprovalService:
    """Durable ApprovalService backed by SQLite — mirrors MockApprovalService semantics."""

    def __init__(self, *, repository: SqliteApprovalRepository) -> None:
        self._repo = repository
        self._lock = asyncio.Lock()

    async def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        from ra_agent.database.models import ApprovalRequestRow

        async with self._lock:
            existing = await self._repo.get_request(approval.approval_id)
            if existing is not None:
                raise ValueError(f"Approval already exists: {approval.approval_id}")
            row = ApprovalRequestRow(
                approval_id=approval.approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                request_id=approval.request_id,
                tool_name=approval.tool_name,
                request_fingerprint=approval.request_fingerprint,
                reason=approval.reason,
                requested_at=approval.requested_at.isoformat(),
                expires_at=approval.expires_at.isoformat(),
                status=approval.status.value,
            )
            await self._repo.create_request(row)
            return approval

    async def list_for_task(self, task_id: str) -> list[ApprovalRequest]:
        async with self._lock:
            rows = await self._repo.list_requests_for_task(task_id)
            return [
                ApprovalRequest(
                    approval_id=row.approval_id,
                    task_id=row.task_id,
                    step_id=row.step_id,
                    request_id=row.request_id,
                    tool_name=row.tool_name,
                    request_fingerprint=row.request_fingerprint,
                    reason=row.reason,
                    requested_at=datetime.fromisoformat(row.requested_at),
                    expires_at=datetime.fromisoformat(row.expires_at),
                    status=ApprovalStatus(row.status),
                )
                for row in rows
            ]

    async def get_request(self, approval_id: str) -> ApprovalRequest:
        async with self._lock:
            row = await self._repo.get_request(approval_id)
            if row is None:
                raise KeyError(f"Unknown approval: {approval_id}")
            return ApprovalRequest(
                approval_id=row.approval_id,
                task_id=row.task_id,
                step_id=row.step_id,
                request_id=row.request_id,
                tool_name=row.tool_name,
                request_fingerprint=row.request_fingerprint,
                reason=row.reason,
                requested_at=datetime.fromisoformat(row.requested_at),
                expires_at=datetime.fromisoformat(row.expires_at),
                status=ApprovalStatus(row.status),
            )

    async def get_decision(self, approval_id: str) -> ApprovalDecision | None:
        async with self._lock:
            approval = await self._repo.get_request(approval_id)
            if approval is None:
                raise KeyError(f"Unknown approval: {approval_id}")
            await self._expire_if_overdue(approval)
            decision_row = await self._repo.get_decision(approval_id)
            if decision_row is None:
                return None
            return ApprovalDecision(
                approval_id=decision_row.approval_id,
                task_id=decision_row.task_id,
                step_id=decision_row.step_id,
                request_id=decision_row.request_id,
                status=ApprovalStatus(decision_row.status),
                decided_by=decision_row.decided_by,
                decided_at=datetime.fromisoformat(decision_row.decided_at),
                reason=decision_row.reason,
            )

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
        from ra_agent.database.models import ApprovalDecisionRow

        if status in {ApprovalStatus.GRANTED, ApprovalStatus.DENIED} and not decided_by.strip():
            raise ValueError("Approval decisions require a nonblank approver identity")
        async with self._lock:
            approval = await self._repo.get_request(approval_id)
            if approval is None:
                raise KeyError(f"Unknown approval: {approval_id}")
            await self._expire_if_overdue(approval)
            decision_row = ApprovalDecisionRow(
                approval_id=approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                request_id=approval.request_id,
                status=status.value,
                decided_by=decided_by,
                decided_at=decided_at.isoformat(),
                reason=reason,
                consumed=0,
            )
            await self._repo.save_decision(decision_row)
            return ApprovalDecision(
                approval_id=approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                request_id=approval.request_id,
                status=status,
                decided_by=decided_by,
                decided_at=decided_at,
                reason=reason,
            )

    async def consume(self, approval_id: str) -> ApprovalDecision:
        async with self._lock:
            if await self._repo.is_consumed(approval_id):
                raise ValueError(f"Approval already consumed: {approval_id}")
            approval = await self._repo.get_request(approval_id)
            if approval is None:
                raise KeyError(f"Unknown approval: {approval_id}")
            await self._expire_if_overdue(approval)
            decision_row = await self._repo.get_decision(approval_id)
            if decision_row is None:
                raise ValueError(f"Approval is still pending: {approval_id}")
            await self._repo.consume_once(approval_id)
            return ApprovalDecision(
                approval_id=decision_row.approval_id,
                task_id=decision_row.task_id,
                step_id=decision_row.step_id,
                request_id=decision_row.request_id,
                status=ApprovalStatus(decision_row.status),
                decided_by=decision_row.decided_by,
                decided_at=datetime.fromisoformat(decision_row.decided_at),
                reason=decision_row.reason,
            )

    async def _expire_if_overdue(self, approval: ApprovalRequestRow) -> None:

        from ra_agent.database.models import ApprovalDecisionRow

        existing = await self._repo.get_decision(approval.approval_id)
        if existing is not None:
            return
        if datetime.now(UTC) >= datetime.fromisoformat(approval.expires_at):
            decision_row = ApprovalDecisionRow(
                approval_id=approval.approval_id,
                task_id=approval.task_id,
                step_id=approval.step_id,
                request_id=approval.request_id,
                status=ApprovalStatus.EXPIRED.value,
                decided_by="system",
                decided_at=datetime.now(UTC).isoformat(),
                reason="approval expired",
                consumed=0,
            )
            try:
                await self._repo.save_decision(decision_row)
            except ValueError:
                return
