"""Consecutive-failure tracking and alert/recovery transitions. Pure logic, no I/O.

State machine per endpoint:

    unknown/healthy --(N failures in a row)--> unhealthy      emits "alert"    (once)
    unhealthy       --(one success)---------> healthy         emits "recovery" (once)

Further failures while already unhealthy emit nothing, so an outage produces exactly one
alert no matter how many checks fail.
"""
from dataclasses import dataclass

UNKNOWN = "unknown"
HEALTHY = "healthy"
UNHEALTHY = "unhealthy"

ALERT = "alert"
RECOVERY = "recovery"


@dataclass(frozen=True)
class StateView:
    status: str = UNKNOWN
    consecutive_failures: int = 0


@dataclass(frozen=True)
class Transition:
    status: str
    consecutive_failures: int
    event: str | None = None


def next_state(state: StateView, success: bool, failure_threshold: int) -> Transition:
    if success:
        event = RECOVERY if state.status == UNHEALTHY else None
        return Transition(HEALTHY, 0, event)

    failures = state.consecutive_failures + 1
    if state.status == UNHEALTHY:
        return Transition(UNHEALTHY, failures, None)
    if failures >= failure_threshold:
        return Transition(UNHEALTHY, failures, ALERT)
    return Transition(state.status, failures, None)


def display_status(status: str, consecutive_failures: int, enabled: bool = True) -> str:
    """Label shown to users: adds 'degraded' (failing, but below the alert threshold)."""
    if not enabled:
        return "disabled"
    if status == UNHEALTHY:
        return UNHEALTHY
    if consecutive_failures > 0:
        return "degraded"
    return status
