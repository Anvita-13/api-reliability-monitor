import pytest

from app.core.checker import CheckSpec, build_client, run_check


@pytest.fixture(scope="module")
def client():
    c = build_client(allow_private=True)
    yield c
    c.close()


def check(client, url, **kw):
    return run_check(CheckSpec(url, timeout_seconds=kw.pop("timeout_seconds", 2), **kw), client)


def test_success(client, mock):
    o = check(client, mock.url("/ok"))
    assert o.success and o.status_code == 200 and o.error_type is None
    assert o.latency_ms is not None and o.latency_ms >= 0


def test_unexpected_status_is_a_failure(client, mock):
    o = check(client, mock.url("/status/503"))
    assert not o.success and o.status_code == 503 and o.error_type == "unexpected_status"
    assert "503" in o.error_message


def test_expected_status_other_than_200(client, mock):
    assert check(client, mock.url("/status/404"), expected_status=404).success


def test_redirect_is_reported_not_followed(client, mock):
    o = check(client, mock.url("/redirect"))
    assert o.status_code == 302 and not o.success
    assert check(client, mock.url("/redirect"), expected_status=302).success


def test_latency_is_measured(client, mock):
    o = check(client, mock.url("/slow/300"))
    assert o.success and 280 <= o.latency_ms < 2000


def test_slow_response_fails_when_above_the_latency_threshold(client, mock):
    o = check(client, mock.url("/slow/300"), latency_threshold_ms=100)
    assert not o.success and o.error_type == "slow_response" and o.status_code == 200


def test_timeout(client, mock):
    o = check(client, mock.url("/slow/3000"), timeout_seconds=1)
    assert not o.success and o.error_type == "timeout" and o.status_code is None
    assert 900 <= o.latency_ms < 2500
    assert "1" in o.error_message


def test_connection_refused(client, closed_port):
    # Windows takes ~2s to report a refused loopback connection, so allow more than that
    o = check(client, f"http://127.0.0.1:{closed_port}/", timeout_seconds=8)
    assert not o.success and o.error_type == "connection_error" and o.status_code is None


def test_unresolvable_host(client):
    o = check(client, "http://no-such-host.invalid/")
    assert not o.success and o.error_type == "connection_error"


def test_unsupported_scheme_is_recorded_not_raised(client):
    o = check(client, "ftp://example.com/")
    assert not o.success and o.error_type == "invalid_url"


def test_head_method(client, mock):
    o = check(client, mock.url("/ok"), method="HEAD")
    assert o.success and o.status_code == 200


def test_never_raises_on_garbage(client):
    o = check(client, "http://[invalid")
    assert not o.success and o.error_type is not None


def test_error_messages_are_bounded_single_line(client, closed_port):
    o = check(client, f"http://127.0.0.1:{closed_port}/", timeout_seconds=8)
    assert len(o.error_message) <= 200 and "\n" not in o.error_message
