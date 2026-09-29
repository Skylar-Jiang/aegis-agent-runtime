from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import SplitResult, urlsplit


class DownloadGuardError(ValueError):
    """Base error raised by download URL and network-boundary validation."""


class DownloadUrlError(DownloadGuardError):
    """Raised when a URL is syntactically invalid or uses a forbidden scheme."""


class UnsafeDownloadAddressError(DownloadGuardError):
    """Raised when DNS or a connected peer resolves to a non-public address."""


class DnsResolutionError(DownloadGuardError):
    """Raised when a hostname cannot be resolved safely."""


class DnsResolver(Protocol):
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]: ...


class SystemDnsResolver:
    async def resolve(self, hostname: str, port: int) -> tuple[str, ...]:
        try:
            results = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                0,
                socket.SOCK_STREAM,
            )
        except OSError as error:
            raise DnsResolutionError(f"could not resolve download host: {hostname}") from error

        addresses: list[str] = []
        for _family, _type, _protocol, _canonical, sockaddr in results:
            address = str(sockaddr[0])
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise DnsResolutionError(f"download host resolved to no addresses: {hostname}")
        return tuple(addresses)


@dataclass(frozen=True, slots=True)
class ValidatedDownloadUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    approved_addresses: tuple[str, ...]


class DownloadNetworkGuard:
    """Enforce protocol, DNS, address and connected-peer SSRF boundaries."""

    def __init__(self, resolver: DnsResolver | None = None) -> None:
        self._resolver = resolver or SystemDnsResolver()

    async def validate_url(self, raw_url: str) -> ValidatedDownloadUrl:
        parsed = self.parse_url(raw_url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            addresses = await self._resolver.resolve(parsed.hostname or "", port)
        except DownloadGuardError:
            raise
        except Exception as error:
            raise DnsResolutionError("download DNS resolution failed") from error

        normalized: list[str] = []
        for address in addresses:
            public = self.require_public_address(address)
            canonical = str(public)
            if canonical not in normalized:
                normalized.append(canonical)
        if not normalized:
            raise DnsResolutionError("download hostname resolved to no public addresses")

        return ValidatedDownloadUrl(
            url=raw_url,
            scheme=parsed.scheme,
            hostname=parsed.hostname or "",
            port=port,
            approved_addresses=tuple(normalized),
        )

    @staticmethod
    def parse_url(raw_url: str) -> SplitResult:
        if not isinstance(raw_url, str) or not raw_url or raw_url.isspace():
            raise DownloadUrlError("download URL must be a non-empty string")
        if any(ord(character) < 32 for character in raw_url):
            raise DownloadUrlError("download URL contains control characters")

        try:
            parsed = urlsplit(raw_url)
            _ = parsed.port
        except ValueError as error:
            raise DownloadUrlError("download URL contains an invalid port or host") from error

        if parsed.scheme.lower() not in {"http", "https"}:
            raise DownloadUrlError("download URL scheme must be http or https")
        if parsed.scheme != parsed.scheme.lower():
            raise DownloadUrlError("download URL scheme must be lowercase")
        if not parsed.hostname:
            raise DownloadUrlError("download URL must include a hostname")
        if parsed.username is not None or parsed.password is not None:
            raise DownloadUrlError("download URL must not contain credentials")
        if parsed.fragment:
            raise DownloadUrlError("download URL fragments are not permitted")
        if parsed.hostname.endswith("."):
            raise DownloadUrlError("download hostname must not end with a dot")
        return parsed

    @staticmethod
    def require_public_address(raw_address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as error:
            raise UnsafeDownloadAddressError(
                f"download peer address is invalid: {raw_address}"
            ) from error

        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped

        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        ):
            raise UnsafeDownloadAddressError(
                f"download address is not publicly routable: {address}"
            )
        return address

    @classmethod
    def validate_peer_address(
        cls,
        raw_address: str,
        approved_addresses: tuple[str, ...],
    ) -> None:
        peer = str(cls.require_public_address(raw_address))
        if peer not in approved_addresses:
            raise UnsafeDownloadAddressError(
                "connected download peer was not in the prevalidated DNS result"
            )
