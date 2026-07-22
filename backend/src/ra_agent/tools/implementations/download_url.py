from __future__ import annotations

import asyncio
import os
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin

import httpx

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.execution.artifacts import (
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.quarantine import (
    FilesystemQuarantineStore,
    QuarantineConflictError,
    QuarantineNotFoundError,
    QuarantineRecord,
    QuarantineStatus,
)
from ra_agent.tools.download_guard import DownloadNetworkGuard


class DownloadToolError(RuntimeError):
    """Base error raised by the quarantined download handler."""


class DownloadResponseError(DownloadToolError):
    """Raised when an HTTP response cannot be accepted."""


class DownloadRedirectError(DownloadToolError):
    """Raised when a redirect is malformed, cyclic, or excessive."""


class DownloadSizeLimitError(DownloadToolError):
    """Raised when response metadata or streamed bytes exceed the limit."""


class DownloadPeerVerificationError(DownloadToolError):
    """Raised when the connected peer address cannot be verified."""


class DownloadUrlHandler:
    """Download HTTP(S) content into quarantine without touching the workspace."""

    TOOL_NAME = "download_url"
    REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        quarantine_store: FilesystemQuarantineStore,
        network_guard: DownloadNetworkGuard,
        *,
        max_redirects: int = 5,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 10.0,
        total_timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
        require_peer_verification: bool = True,
    ) -> None:
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        for name, value in (
            ("connect_timeout_seconds", connect_timeout_seconds),
            ("read_timeout_seconds", read_timeout_seconds),
            ("total_timeout_seconds", total_timeout_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        self._store = quarantine_store
        self._network_guard = network_guard
        self._max_redirects = max_redirects
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._client = client
        self._require_peer_verification = require_peer_verification

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        if request.tool_name != self.TOOL_NAME:
            raise DownloadToolError(f"handler only supports {self.TOOL_NAME}")

        raw_url = request.arguments.get("url")
        if not isinstance(raw_url, str):
            raise DownloadToolError("download_url argument 'url' must be a string")

        existing = await self._load_existing(request, raw_url)
        if existing is not None:
            return self._build_result(request, existing)

        temporary_path: Path | None = None
        record: QuarantineRecord | None = None
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                if self._client is None:
                    timeout = httpx.Timeout(
                        connect=self._connect_timeout_seconds,
                        read=self._read_timeout_seconds,
                        write=self._read_timeout_seconds,
                        pool=self._connect_timeout_seconds,
                    )
                    async with httpx.AsyncClient(
                        timeout=timeout,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client:
                        download = await self._download(client, raw_url, request.request_id)
                else:
                    download = await self._download(
                        self._client,
                        raw_url,
                        request.request_id,
                    )

                temporary_path = download.temporary_path
                record = await self._store.stage(
                    request,
                    source_url=raw_url,
                    final_url=download.final_url,
                    redirect_chain=download.redirect_chain,
                    temporary_path=temporary_path,
                    content_type=download.content_type,
                    content_sha256=download.content_sha256,
                    size_bytes=download.size_bytes,
                    http_status=download.http_status,
                )
                temporary_path = None

            if record.status is not QuarantineStatus.QUARANTINED:
                raise QuarantineConflictError(
                    f"download request_id already reached terminal state: {record.status.value}"
                )
            return self._build_result(request, record)
        except asyncio.CancelledError:
            self._safe_unlink(temporary_path)
            if record is not None and record.status is QuarantineStatus.QUARANTINED:
                await asyncio.shield(
                    self._store.mark_rolled_back(
                        request.request_id,
                        reason="download execution cancelled",
                    )
                )
            await asyncio.shield(self._store.cleanup(request.request_id))
            raise
        except Exception:
            self._safe_unlink(temporary_path)
            if record is not None and record.status is QuarantineStatus.QUARANTINED:
                try:
                    await self._store.mark_rolled_back(
                        request.request_id,
                        reason="download result construction failed",
                    )
                except Exception:
                    pass
            await self._store.cleanup(request.request_id)
            raise

    async def _load_existing(
        self,
        request: ToolCallRequest,
        source_url: str,
    ) -> QuarantineRecord | None:
        try:
            record = await self._store.get(request.request_id)
        except QuarantineNotFoundError:
            return None

        if (
            record.task_id != request.task_id
            or record.step_id != request.step_id
            or record.tool_name != request.tool_name
            or record.source_url != source_url
        ):
            raise QuarantineConflictError(
                "request_id already exists with different download semantics"
            )
        if not await self._store.verify_integrity(request.request_id):
            raise DownloadToolError("existing quarantine record failed integrity verification")
        if record.status is not QuarantineStatus.QUARANTINED:
            raise QuarantineConflictError(
                f"download request_id already reached terminal state: {record.status.value}"
            )
        return record

    async def _download(
        self,
        client: httpx.AsyncClient,
        source_url: str,
        request_id: str,
    ) -> _CompletedDownload:
        current_url = source_url
        redirect_chain: list[str] = []
        visited: set[str] = set()

        while True:
            if current_url in visited:
                raise DownloadRedirectError("download redirect loop detected")
            visited.add(current_url)

            validated = await self._network_guard.validate_url(current_url)
            request = client.build_request(
                "GET",
                current_url,
                headers={
                    "Accept": "*/*",
                    "User-Agent": "Aegis-Runtime-Downloader/1.0",
                },
            )
            response = await client.send(request, stream=True, follow_redirects=False)
            try:
                self._validate_connected_peer(response, validated.approved_addresses)

                if response.status_code in self.REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if location is None or not location.strip():
                        raise DownloadRedirectError("redirect response is missing Location")
                    if len(redirect_chain) >= self._max_redirects:
                        raise DownloadRedirectError("download exceeded redirect limit")
                    next_url = urljoin(current_url, location)
                    # Parse immediately, then DNS/address validation occurs at the next loop.
                    self._network_guard.parse_url(next_url)
                    if next_url in visited:
                        raise DownloadRedirectError("download redirect loop detected")
                    redirect_chain.append(next_url)
                    current_url = next_url
                    continue

                if not 200 <= response.status_code < 300:
                    raise DownloadResponseError(
                        f"download returned unexpected HTTP status: {response.status_code}"
                    )

                content_length = self._parse_content_length(response.headers)
                if content_length is not None and content_length > self._store.max_download_bytes:
                    raise DownloadSizeLimitError("download Content-Length exceeds configured limit")

                temporary_path = self._store.create_temporary_path(request_id)
                try:
                    digest = sha256()
                    size_bytes = 0
                    with temporary_path.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            size_bytes += len(chunk)
                            if size_bytes > self._store.max_download_bytes:
                                raise DownloadSizeLimitError(
                                    "streamed download exceeds configured limit"
                                )
                            stream.write(chunk)
                            digest.update(chunk)
                        stream.flush()
                        os.fsync(stream.fileno())

                    content_type = response.headers.get("content-type")
                    if content_type is None or not content_type.strip():
                        content_type = "application/octet-stream"
                    return _CompletedDownload(
                        temporary_path=temporary_path,
                        final_url=current_url,
                        redirect_chain=tuple(redirect_chain),
                        content_type=content_type.strip(),
                        content_sha256=digest.hexdigest(),
                        size_bytes=size_bytes,
                        http_status=response.status_code,
                    )
                except Exception:
                    self._safe_unlink(temporary_path)
                    raise
            finally:
                await response.aclose()

    def _validate_connected_peer(
        self,
        response: httpx.Response,
        approved_addresses: tuple[str, ...],
    ) -> None:
        network_stream = response.extensions.get("network_stream")
        if network_stream is None:
            if self._require_peer_verification:
                raise DownloadPeerVerificationError(
                    "HTTP response did not expose the connected peer address"
                )
            return

        get_extra_info = getattr(network_stream, "get_extra_info", None)
        if not callable(get_extra_info):
            raise DownloadPeerVerificationError(
                "HTTP network stream cannot expose the connected peer address"
            )
        peer = get_extra_info("server_addr")
        if isinstance(peer, tuple) and peer:
            raw_address = str(peer[0])
        elif isinstance(peer, str):
            raw_address = peer
        else:
            raise DownloadPeerVerificationError("connected peer address is unavailable")
        self._network_guard.validate_peer_address(raw_address, approved_addresses)

    @staticmethod
    def _parse_content_length(headers: httpx.Headers) -> int | None:
        values = headers.get_list("content-length")
        if not values:
            return None
        normalized: list[str] = []
        for value in values:
            normalized.extend(part.strip() for part in value.split(","))
        if not normalized or any(not value.isdigit() for value in normalized):
            raise DownloadResponseError("Content-Length header is invalid")
        if len(set(normalized)) != 1:
            raise DownloadResponseError("Content-Length headers disagree")
        return int(normalized[0])

    @staticmethod
    def _safe_unlink(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _build_result(
        request: ToolCallRequest,
        record: QuarantineRecord,
    ) -> ToolExecutionResult:
        output = {
            "downloaded": True,
            "source_url": record.source_url,
            "final_url": record.final_url,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "sha256": record.content_sha256,
            "status": record.status.value,
        }
        artifacts = [
            build_tool_output_artifact(
                request,
                output,
                status=ExecutionStatus.PENDING_COMMIT,
            ),
            build_quarantined_download_artifact(
                request,
                quarantine_path=record.quarantine_path,
                source_url=record.source_url,
                final_url=record.final_url,
                content_sha256=record.content_sha256,
                size_bytes=record.size_bytes,
                content_type=record.content_type,
            ),
        ]
        pending_change = {
            "operation": "DOWNLOAD",
            "quarantine_path": record.quarantine_path,
            "source_url": record.source_url,
            "final_url": record.final_url,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
            "status": record.status.value,
        }
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.PENDING_COMMIT,
            output=output,
            artifacts=artifacts,
            pending_changes=[pending_change],
        )


class _CompletedDownload:
    def __init__(
        self,
        *,
        temporary_path: Path,
        final_url: str,
        redirect_chain: tuple[str, ...],
        content_type: str,
        content_sha256: str,
        size_bytes: int,
        http_status: int,
    ) -> None:
        self.temporary_path = temporary_path
        self.final_url = final_url
        self.redirect_chain = redirect_chain
        self.content_type = content_type
        self.content_sha256 = content_sha256
        self.size_bytes = size_bytes
        self.http_status = http_status
