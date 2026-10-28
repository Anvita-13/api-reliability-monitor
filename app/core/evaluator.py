"""Decide whether a single check passed. Pure logic, no I/O."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    success: bool
    error_type: str | None = None
    error_message: str | None = None


def evaluate(
    status_code: int,
    expected_status: int,
    latency_ms: int,
    latency_threshold_ms: int | None = None,
) -> Verdict:
    """A response passes when the status matches and (optionally) it was fast enough."""
    if status_code != expected_status:
        return Verdict(False, "unexpected_status", f"Expected HTTP {expected_status}, got {status_code}")
    if latency_threshold_ms is not None and latency_ms > latency_threshold_ms:
        return Verdict(False, "slow_response",
                       f"Response took {latency_ms} ms (limit {latency_threshold_ms} ms)")
    return Verdict(True)
