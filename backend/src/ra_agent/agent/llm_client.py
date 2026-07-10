from typing import Protocol


class LLMClient(Protocol):
    async def complete(self, messages: list[dict[str, str]]) -> str: ...


class MockLLMClient:
    """Offline Phase 0 mock; it never calls an external model."""

    async def complete(self, messages: list[dict[str, str]]) -> str:
        return f"mock response for {len(messages)} message(s)"
