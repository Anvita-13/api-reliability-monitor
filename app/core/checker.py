"""Send one HTTP request, time it, and turn the result (or the failure) into a CheckOutcome."""
import logging
import time
from dataclasses import dataclass

import httpx

from app.core.evaluator import evaluate
from app.core.url_guard import BlockedDestinationError, GuardedTransport

logger = logging.getLogger(__name__)

USER_AGENT = "api-reliability-monitor/1.0"
MAX_MESSAGE_LENGTH = 200


@dataclass(frozen=True)
class CheckSpec:
    url: str
    method: str = "GET"
    expected_status: int = 200
    timeout_seconds: float = 5
    latency_threshold_ms: int | None = None


@dataclass(frozen=True)
class CheckOutcome:
    success: bool
    status_code: int | None
    latency_ms: int | None
    error_type: str | None = None
    error_message: str | None = None


def build_client(allow_private: bool = False) -> httpx.Client:
    """Shared HTTP client for the process. Redirects are NOT followed: a 3xx answer is a result
    (and following redirects could bounce the request to a forbidden destination)."""
    return httpx.Client(
        transport=GuardedTransport(allow_private=allow_private),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )


def _clean(message: str) -> str:
    """One line, bounded length, nothing that could carry a secret from the request."""
    return " ".join(str(message).split())[:MAX_MESSAGE_LENGTH]


def run_check(spec: CheckSpec, client: httpx.Client) -> CheckOutcome:
    """Execute a check. Never raises: every failure becomes a recorded outcome."""
    started = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        timeout = httpx.Timeout(spec.timeout_seconds)
        # stream() returns as soon as the response headers arrive, so latency is
        # "time to first response" and a huge body cannot slow the monitor down.
        with client.stream(spec.method, spec.url, timeout=timeout) as response:
            status_code = response.status_code
            latency = elapsed_ms()
    except BlockedDestinationError as exc:
        return CheckOutcome(False, None, elapsed_ms(), "blocked_destination", _clean(exc))
    except httpx.TimeoutException:
        return CheckOutcome(False, None, elapsed_ms(), "timeout",
                            f"No response within {spec.timeout_seconds} seconds")
    except httpx.ConnectError as exc:
        text = str(exc)
        if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
            return CheckOutcome(False, None, elapsed_ms(), "ssl_error", _clean(text))
        return CheckOutcome(False, None, elapsed_ms(), "connection_error", _clean(text) or "Connection failed")
    except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
        return CheckOutcome(False, None, elapsed_ms(), "invalid_url", _clean(exc))
    except httpx.RequestError as exc:
        return CheckOutcome(False, None, elapsed_ms(), "request_error", _clean(f"{type(exc).__name__}: {exc}"))
    except Exception as exc:  # noqa: BLE001 - the worker must survive anything
        logger.exception("Unexpected error while checking an endpoint")
        return CheckOutcome(False, None, elapsed_ms(), "internal_error", _clean(type(exc).__name__))

    verdict = evaluate(status_code, spec.expected_status, latency, spec.latency_threshold_ms)
    return CheckOutcome(verdict.success, status_code, latency, verdict.error_type, verdict.error_message)
