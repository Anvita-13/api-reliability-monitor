import ipaddress
import socket

import httpcore
import pytest

from app.core import url_guard
from app.core.checker import CheckSpec, build_client, run_check
from app.core.url_guard import (
    BlockedDestinationError,
    GuardedBackend,
    InvalidURLError,
    is_blocked_ip,
    parse_url,
    validate_target_url,
)


def fake_dns(monkeypatch, *answers):
    """Make getaddrinfo return the given IP lists in order (the last one repeats)."""
    calls = {"n": 0}

    def getaddrinfo(host, port, *a, **kw):
        ips = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        if ips is None:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", getaddrinfo)
    return calls


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.1.2.3", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254",  # cloud metadata
    "0.0.0.0", "100.64.0.1", "224.0.0.1", "255.255.255.255", "::1", "fe80::1", "fc00::1", "::",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254", "64:ff9b::a00:1",
])
def test_non_public_addresses_are_blocked(ip):
    assert is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_allowed(ip):
    assert not is_blocked_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("url", [
    "", "   ", "ftp://example.com", "file:///etc/passwd", "javascript:alert(1)", "//example.com", "example.com",
    "http://", "http://user:pass@example.com", "http://exa mple.com", "http://example.com:99999",
    "http://example.com/\x00", "gopher://example.com", "http://" + "a" * 3000,
])
def test_malformed_or_forbidden_urls_are_rejected(url):
    with pytest.raises(InvalidURLError):
        parse_url(url)


def test_normal_urls_parse():
    assert parse_url("https://api.example.com:8443/v1/health?x=1").hostname == "api.example.com"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
    "http://10.1.2.3:8080/", "http://[::1]/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/",
])
def test_private_targets_are_rejected_at_registration(url, monkeypatch):
    fake_dns(monkeypatch, ["127.0.0.1"])  # "localhost" style names resolve to loopback
    with pytest.raises(BlockedDestinationError):
        validate_target_url(url, allow_private=False)


def test_private_targets_are_accepted_when_explicitly_allowed():
    assert validate_target_url("http://127.0.0.1:9100/ok", allow_private=True).startswith("http://127.0.0.1")


def test_hostname_resolving_to_a_private_address_is_rejected(monkeypatch):
    fake_dns(monkeypatch, ["10.0.0.7"])
    with pytest.raises(BlockedDestinationError):
        validate_target_url("https://internal.example.com", allow_private=False)


def test_hostname_with_any_private_answer_is_rejected(monkeypatch):
    fake_dns(monkeypatch, ["93.184.216.34", "192.168.0.9"])
    with pytest.raises(BlockedDestinationError):
        validate_target_url("https://mixed.example.com", allow_private=False)


def test_public_hostname_is_accepted_and_fragment_removed(monkeypatch):
    fake_dns(monkeypatch, ["93.184.216.34"])
    assert validate_target_url("https://example.com/path#frag", allow_private=False) == "https://example.com/path"


def test_unresolvable_hostname_is_accepted(monkeypatch):
    # monitoring an API that is down right now is the whole point
    fake_dns(monkeypatch, None)
    assert validate_target_url("https://down.example.com", allow_private=False)


def test_connection_time_guard_blocks_private_addresses(monkeypatch):
    fake_dns(monkeypatch, ["127.0.0.1"])
    with pytest.raises(BlockedDestinationError):
        GuardedBackend(allow_private=False).connect_tcp("evil.example.com", 80, timeout=1)


def test_dns_failure_at_connection_time_is_a_connect_error(monkeypatch):
    fake_dns(monkeypatch, None)
    with pytest.raises(httpcore.ConnectError):
        GuardedBackend(allow_private=False).connect_tcp("nx.example.com", 80, timeout=1)


def test_dns_rebinding_is_stopped_at_connection_time(monkeypatch):
    """Name looks public when registered, then answers with an internal address."""
    fake_dns(monkeypatch, ["93.184.216.34"], ["169.254.169.254"])
    assert validate_target_url("http://rebind.example.com/", allow_private=False)  # passes registration
    outcome = run_check(CheckSpec("http://rebind.example.com/", timeout_seconds=2), build_client(allow_private=False))
    assert not outcome.success and outcome.error_type == "blocked_destination"


def test_checker_refuses_loopback_when_private_targets_are_off(mock):
    outcome = run_check(CheckSpec(mock.url("/ok"), timeout_seconds=2), build_client(allow_private=False))
    assert outcome.error_type == "blocked_destination"
    assert mock.hits.get("/ok", 0) == 0  # the request was never sent


def test_redirects_are_not_followed(mock):
    outcome = run_check(CheckSpec(mock.url("/redirect"), timeout_seconds=2), build_client(allow_private=True))
    assert outcome.status_code == 302 and outcome.error_type == "unexpected_status"
    assert mock.hits.get("/ok", 0) == 0  # the redirect target was never contacted
