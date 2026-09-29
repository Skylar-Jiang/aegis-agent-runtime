from __future__ import annotations

from typing import Any, Protocol

from ra_agent.contracts.core_v1 import ToolCallEnvelope


class EventStore(Protocol):
    async def append_event(self, event: dict[str, Any]) -> dict[str, Any]: ...

    async def list_task_events(self, task_id: str) -> list[dict[str, Any]]: ...

    async def get_chain_head(self, task_id: str) -> str | None: ...


class SignatureProvider(Protocol):
    def sign_sm2(self, payload: bytes, *, key_id: str) -> str: ...

    def verify_sm2(self, payload: bytes, signature: str, *, key_id: str) -> bool: ...

    def list_public_keys(self) -> dict[str, str]: ...


class EvidenceRecorder(Protocol):
    async def record_object(
        self,
        *,
        task_id: str,
        object_type: str,
        payload: dict[str, Any],
    ) -> str: ...

    async def list_task_objects(
        self,
        task_id: str,
        references: set[str],
    ) -> list[dict[str, Any]]: ...


class ToolExecutorAdapter(Protocol):
    async def execute(
        self, envelope: ToolCallEnvelope, *, gateway_token: object | None = None
    ) -> dict[str, Any]: ...
