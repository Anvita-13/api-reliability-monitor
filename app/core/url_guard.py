"""SSRF protection.

A monitor fetches URLs that users give it, so without protection it could be pointed at
internal services or the cloud metadata endpoint (169.254.169.254) to steal credentials.

Two layers:
1. ``validate_target_url`` rejects bad URLs when an endpoint is registered or edited.
2. ``GuardedBackend`` re-checks at *connection time*: it resolves the host name itself,
   refuses non-public addresses, and connects to the vetted IP. That also defeats DNS
   rebinding (a name that answered "public" at registration and "internal" later).
"""
import ipaddress
import socket
from urllib.parse import urlsplit

import httpcore
import httpx

MAX_URL_LENGTH = 2048
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class InvalidURLError(ValueError):
    """The URL is malformed or uses a scheme/format that is not allowed."""


class BlockedDestinationError(Exception):
    """The host resolves to an address that must not be contacted."""


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for anything that is not an ordinary public internet address."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:  # ::ffff:10.0.0.1 is really 10.0.0.1
            return is_blocked_ip(ip.ipv4_mapped)
        if ip in _NAT64:  # 64:ff9b::a00:1 embeds an IPv4 address
            return is_blocked_ip(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified or not ip.is_global
    )


def parse_url(url: str):
    """Syntax checks only (no network). Returns the parsed URL."""
    if not isinstance(url, str) or not url.strip():
        raise InvalidURLError("URL is required.")
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise InvalidURLError(f"URL is longer than {MAX_URL_LENGTH} characters.")
    if any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise InvalidURLError("URL must not contain spaces or control characters.")
    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError for an invalid port
    except ValueError as exc:
        raise InvalidURLError("URL is not valid.") from exc
    if parts.scheme not in ("http", "https"):
        raise InvalidURLError("Only http:// and https:// URLs are allowed.")
    if not parts.hostname:
        raise InvalidURLError("URL must include a host name.")
    if parts.username is not None or parts.password is not None:
        raise InvalidURLError("URLs with embedded credentials are not allowed.")
    if port is not None and not 1 <= port <= 65535:
        raise InvalidURLError("URL port is out of range.")
    return parts


def resolve_public_addresses(host: str, port: int, allow_private: bool) -> list[str]:
    """Resolve ``host`` and return its IPs; raise if any is blocked (unless allow_private).

    Raises ``socket.gaierror`` when the name does not resolve.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    ips: list[str] = []
    for info in infos:
        ip_text = info[4][0].split("%")[0]  # drop IPv6 zone id
        if ip_text not in ips:
            ips.append(ip_text)
    if not allow_private:
        for ip_text in ips:
            if is_blocked_ip(ipaddress.ip_address(ip_text)):
                raise BlockedDestinationError(
                    "The host resolves to a private or otherwise non-public address, which is not allowed."
                )
    return ips


def validate_target_url(url: str, allow_private: bool = False) -> str:
    """Validate a URL for registration. Returns the cleaned URL (fragment removed).

    A name that does not resolve *yet* is accepted (monitoring a currently-down host is the
    whole point); a name that resolves to a blocked address is rejected.
    """
    parts = parse_url(url)
    host = parts.hostname
    cleaned = parts._replace(fragment="").geturl()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not allow_private and is_blocked_ip(literal):
            raise BlockedDestinationError("That IP address is private or otherwise non-public and cannot be monitored.")
        return cleaned
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        resolve_public_addresses(host, port, allow_private)
    except socket.gaierror:
        pass
    return cleaned


class GuardedBackend(httpcore.SyncBackend):
    """Network backend that vets every address before connecting."""

    def __init__(self, allow_private: bool = False):
        super().__init__()
        self.allow_private = allow_private

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        try:
            ips = resolve_public_addresses(host, port, self.allow_private)
        except socket.gaierror as exc:
            raise httpcore.ConnectError("DNS lookup failed for the host") from exc
        last_error: Exception | None = None
        for ip_text in ips:
            try:
                return super().connect_tcp(ip_text, port, timeout, local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        raise last_error or httpcore.ConnectError("Could not connect")


class GuardedTransport(httpx.HTTPTransport):
    """httpx transport whose connections all go through ``GuardedBackend``.

    Connections are never reused (a monitor should time a fresh connection each check).
    """

    def __init__(self, allow_private: bool = False):
        super().__init__(trust_env=False)
        self._pool.close()
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            max_connections=100,
            max_keepalive_connections=0,
            network_backend=GuardedBackend(allow_private),
        )
