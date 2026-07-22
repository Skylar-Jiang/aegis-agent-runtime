from __future__ import annotations

import pytest

from ra_agent.tools.download_guard import (
    DownloadNetworkGuard,
    DownloadUrlError,
    UnsafeDownloadAddressError,
)


class FakeResolver:
    def __init__(self, addresses: tuple[str, ...]) -> None:
        self.addresses = addresses
        self.calls: list[tuple[str, int]] = []

    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        self.calls.append((hostname, port))
        return self.addresses


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "data:text/plain,hello",
        "javascript:alert(1)",
        "https:///missing-host",
        "https://user:password@example.com/file",
        "https://example.com./file",
        "https://example.com/file#fragment",
    ],
)
async def test_guard_rejects_invalid_or_unsafe_url_syntax(url: str) -> None:
    guard = DownloadNetworkGuard(FakeResolver(("93.184.216.34",)))
    with pytest.raises(DownloadUrlError):
        await guard.validate_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://example.com/a", "https://example.com/a"])
async def test_guard_allows_http_and_https_public_hosts(url: str) -> None:
    resolver = FakeResolver(("93.184.216.34",))
    result = await DownloadNetworkGuard(resolver).validate_url(url)

    assert result.hostname == "example.com"
    assert result.approved_addresses == ("93.184.216.34",)
    assert resolver.calls == [("example.com", 443 if url.startswith("https") else 80)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "::",
        "::ffff:127.0.0.1",
    ],
)
async def test_guard_rejects_non_public_dns_addresses(address: str) -> None:
    guard = DownloadNetworkGuard(FakeResolver((address,)))
    with pytest.raises(UnsafeDownloadAddressError):
        await guard.validate_url("https://example.com/file")


@pytest.mark.asyncio
async def test_guard_rejects_hostname_when_any_dns_result_is_private() -> None:
    guard = DownloadNetworkGuard(FakeResolver(("93.184.216.34", "10.0.0.1")))
    with pytest.raises(UnsafeDownloadAddressError):
        await guard.validate_url("https://example.com/file")


def test_guard_rejects_peer_not_in_prevalidated_dns_results() -> None:
    with pytest.raises(UnsafeDownloadAddressError):
        DownloadNetworkGuard.validate_peer_address(
            "8.8.8.8",
            ("93.184.216.34",),
        )


def test_guard_accepts_matching_public_peer() -> None:
    DownloadNetworkGuard.validate_peer_address(
        "93.184.216.34",
        ("93.184.216.34",),
    )
