"""PR1 substitutes for modules owned by P2/P3.

These are deliberately simple and deterministic. They share the same public method
names that the real EventStore and SignatureProvider must implement in PR2.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy
from typing import Any

from ra_agent.contracts.core_v1 import ToolCallEnvelope


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._heads: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        task_id = str(event["task_id"])
        async with self._lock:
            items = self._events.setdefault(task_id, [])
            stored = deepcopy(event)
            stored.setdefault("sequence", len(items) + 1)
            canonical = json.dumps(stored, sort_keys=True, separators=(",", ":"), default=str)
            previous = self._heads.get(task_id, "")
            head = hashlib.sha256(f"{previous}|{canonical}".encode()).hexdigest()
            stored["fake_chain_head"] = head
            items.append(stored)
            self._heads[task_id] = head
            return deepcopy(stored)

    async def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return deepcopy(self._events.get(task_id, []))

    async def get_chain_head(self, task_id: str) -> str | None:
        async with self._lock:
            return self._heads.get(task_id)


class FakeSignatureProvider:
    """Deterministic test double. This is NOT real SM2 and must not be used as crypto."""

    def __init__(self, key_id: str = "fake-core-key") -> None:
        self.key_id = key_id

    def sign_sm2(self, payload: bytes, *, key_id: str) -> str:
        if key_id != self.key_id:
            raise KeyError(key_id)
        return hashlib.sha256(key_id.encode() + b"|" + payload).hexdigest()

    def verify_sm2(self, payload: bytes, signature: str, *, key_id: str) -> bool:
        try:
            return self.sign_sm2(payload, key_id=key_id) == signature
        except KeyError:
            return False

    def list_public_keys(self) -> dict[str, str]:
        return {self.key_id: "FAKE-ONLY-NOT-A-REAL-SM2-PUBLIC-KEY"}


class DryRunToolExecutor:
    def bind_gateway(self, token: object) -> None:
        self._gateway_token = token

    async def execute(
        self, envelope: ToolCallEnvelope, *, gateway_token: object | None = None
    ) -> dict[str, Any]:
        return {
            "dry_run": True,
            "tool": envelope.tool,
            "action": envelope.action,
            "resource": envelope.resource,
            "canonical_args": envelope.canonical_args,
        }
