from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from ra_agent.contracts import ExecutionStatus, SourceType, ToolCallRequest
from ra_agent.execution.quarantine import (
    FilesystemQuarantineStore,
    QuarantineConflictError,
    QuarantineStatus,
)
from ra_agent.tools.download_guard import (
    DownloadNetworkGuard,
    UnsafeDownloadAddressError,
)
from ra_agent.tools.implementations.download_url import (
    DownloadRedirectError,
    DownloadSizeLimitError,
    DownloadUrlHandler,
)


class MappingResolver:
    def __init__(self, mapping: dict[str, tuple[str, ...]]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        self.calls.append(hostname)
        return self.mapping[hostname]


class FakeNetworkStream:
    def __init__(self, address: str) -> None:
        self.address = address

    def get_extra_info(self, name: str) -> object:
        if name == "server_addr":
            return (self.address, 443)
        return None


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk


class BlockingStream(httpx.AsyncByteStream):
    def __init__(self, started: asyncio.Event) -> None:
        self.started = started

    async def __aiter__(self):
        self.started.set()
        await asyncio.sleep(60)
        yield b"never"


def make_request(
    request_id: str, url: str = "https://example.com/file"
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name="download_url",
        arguments={"url": url},
        objective="download into quarantine",
        context_summary="download handler unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def response_for(
    request: httpx.Request,
    *,
    status: int = 200,
    body: bytes | httpx.AsyncByteStream = b"downloaded",
    headers: dict[str, str] | None = None,
    peer: str = "93.184.216.34",
) -> httpx.Response:
    return httpx.Response(
        status,
        headers=headers,
        content=body if isinstance(body, bytes) else None,
        stream=body if isinstance(body, httpx.AsyncByteStream) else None,
        request=request,
        extensions={"network_stream": FakeNetworkStream(peer)},
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemQuarantineStore:
    return FilesystemQuarantineStore(tmp_path / "quarantine", max_download_bytes=32)


def make_handler(
    store: FilesystemQuarantineStore,
    transport_handler,
    *,
    resolver_mapping: dict[str, tuple[str, ...]] | None = None,
    total_timeout_seconds: float = 2,
) -> DownloadUrlHandler:
    mapping = resolver_mapping or {"example.com": ("93.184.216.34",)}
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    return DownloadUrlHandler(
        store,
        DownloadNetworkGuard(MappingResolver(mapping)),
        client=client,
        total_timeout_seconds=total_timeout_seconds,
    )


@pytest.mark.asyncio
async def test_download_enters_quarantine_and_returns_inspectable_artifact(
    store: FilesystemQuarantineStore,
    tmp_path: Path,
) -> None:
    payload = b"safe download"

    def transport(request: httpx.Request) -> httpx.Response:
        return response_for(
            request,
            body=payload,
            headers={"content-type": "text/plain", "content-length": str(len(payload))},
        )

    handler = make_handler(store, transport)
    result = await handler(make_request("request-success"))
    record = await store.get("request-success")

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert record.status is QuarantineStatus.QUARANTINED
    assert record.content_sha256 == sha256(payload).hexdigest()
    assert (await store.payload_path(record.request_id)).read_bytes() == payload
    assert [artifact["artifact_type"] for artifact in result.artifacts] == [
        "tool_output",
        "quarantined_download",
    ]
    artifact = result.artifacts[1]
    assert artifact["quarantine_path"] == "request-success/payload.bin"
    assert artifact["sha256"] == record.content_sha256
    assert artifact["size_bytes"] == len(payload)
    assert not (tmp_path / "workspace" / "file").exists()
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_content_length_over_limit_is_rejected_and_cleaned(
    store: FilesystemQuarantineStore,
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return response_for(request, headers={"content-length": "1000"})

    handler = make_handler(store, transport)
    with pytest.raises(DownloadSizeLimitError):
        await handler(make_request("request-length-limit"))

    assert not list(store.quarantine_root.glob("*.download.tmp"))
    assert not (store.quarantine_root / "request-length-limit").exists()
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_streaming_over_limit_is_rejected_and_cleaned(
    store: FilesystemQuarantineStore,
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return response_for(request, body=ChunkStream([b"a" * 20, b"b" * 20]))

    handler = make_handler(store, transport)
    with pytest.raises(DownloadSizeLimitError):
        await handler(make_request("request-stream-limit"))

    assert not list(store.quarantine_root.glob("*.download.tmp"))
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_redirect_is_manually_followed_and_revalidated(
    store: FilesystemQuarantineStore,
) -> None:
    calls: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "example.com":
            return response_for(
                request,
                status=302,
                headers={"location": "https://cdn.example.com/file"},
            )
        return response_for(request, body=b"cdn", peer="8.8.8.8")

    handler = make_handler(
        store,
        transport,
        resolver_mapping={
            "example.com": ("93.184.216.34",),
            "cdn.example.com": ("8.8.8.8",),
        },
    )
    result = await handler(make_request("request-redirect"))
    record = await store.get("request-redirect")

    assert calls == ["https://example.com/file", "https://cdn.example.com/file"]
    assert record.final_url == "https://cdn.example.com/file"
    assert record.redirect_chain == ("https://cdn.example.com/file",)
    assert result.output["final_url"] == record.final_url
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_redirect_to_private_address_is_rejected_before_second_request(
    store: FilesystemQuarantineStore,
) -> None:
    calls = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response_for(
            request,
            status=302,
            headers={"location": "http://internal.example/admin"},
        )

    handler = make_handler(
        store,
        transport,
        resolver_mapping={
            "example.com": ("93.184.216.34",),
            "internal.example": ("127.0.0.1",),
        },
    )
    with pytest.raises(UnsafeDownloadAddressError):
        await handler(make_request("request-private-redirect"))

    assert calls == 1
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_redirect_limit_is_enforced(store: FilesystemQuarantineStore) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        current = int(urlsplit(str(request.url)).path.strip("/") or "0")
        return response_for(
            request,
            status=302,
            headers={"location": f"https://example.com/{current + 1}"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    handler = DownloadUrlHandler(
        store,
        DownloadNetworkGuard(MappingResolver({"example.com": ("93.184.216.34",)})),
        client=client,
        max_redirects=2,
    )
    with pytest.raises(DownloadRedirectError):
        await handler(make_request("request-redirect-limit", "https://example.com/0"))
    await client.aclose()


@pytest.mark.asyncio
async def test_same_request_id_is_idempotent_without_second_network_call(
    store: FilesystemQuarantineStore,
) -> None:
    calls = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response_for(request, body=b"stable")

    handler = make_handler(store, transport)
    request = make_request("request-idempotent")
    first = await handler(request)
    second = await handler(request)

    assert first.output == second.output
    assert calls == 1
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_request_id_reuse_with_different_url_conflicts(
    store: FilesystemQuarantineStore,
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return response_for(request, body=b"stable")

    handler = make_handler(store, transport)
    await handler(make_request("request-url-conflict"))

    with pytest.raises(QuarantineConflictError):
        await handler(
            make_request("request-url-conflict", "https://example.com/different")
        )
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_total_timeout_cleans_temporary_download(
    store: FilesystemQuarantineStore,
) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        return response_for(request, body=ChunkStream([b"late"], delay=1))

    handler = make_handler(store, transport, total_timeout_seconds=0.01)
    with pytest.raises(TimeoutError):
        await handler(make_request("request-timeout"))

    assert not list(store.quarantine_root.glob("*.download.tmp"))
    await handler._client.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_cancellation_cleans_temporary_download(
    store: FilesystemQuarantineStore,
) -> None:
    started = asyncio.Event()

    def transport(request: httpx.Request) -> httpx.Response:
        return response_for(request, body=BlockingStream(started))

    handler = make_handler(store, transport, total_timeout_seconds=10)
    task = asyncio.create_task(handler(make_request("request-cancelled")))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not list(store.quarantine_root.glob("*.download.tmp"))
    assert not (store.quarantine_root / "request-cancelled").exists()
    await handler._client.aclose()  # type: ignore[union-attr]
