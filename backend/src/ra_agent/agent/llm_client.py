from typing import Protocol

import httpx


class LLMClient(Protocol):
    async def complete(self, messages: list[dict[str, str]]) -> str: ...


class MockLLMClient:
    """Offline Phase 1 mock; it never calls an external model."""

    async def complete(self, messages: list[dict[str, str]]) -> str:
        return f"mock response for {len(messages)} message(s)"


class DeepSeekClient:
    """Small OpenAI-compatible client for DeepSeek chat completions."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url or not api_key or not model:
            raise ValueError("DeepSeek base_url, api_key, and model are required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(timeout=30)
        self._owns_client = client is None

    async def complete(self, messages: list[dict[str, str]]) -> str:
        response = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "messages": messages},
        )
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError) as error:
            raise ValueError("DeepSeek response has no assistant content") from error
        if not isinstance(content, str):
            raise ValueError("DeepSeek response content must be text")
        return content

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
