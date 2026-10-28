"""The monitoring pipeline: claim due endpoints -> check -> record -> detect -> notify.

Scheduling is database-driven so that several servers can each run a worker without ever
checking the same endpoint twice:

* ``claim_due_endpoints`` locks due rows with ``SELECT ... FOR UPDATE SKIP LOCKED`` and moves
  their ``next_check_at`` forward in the same transaction. A row locked by another worker is
  skipped, never waited on.
* ``record_outcome`` locks the endpoint's alert-state row, so concurrent results for one
  endpoint are applied one at a time and an outage raises exactly one alert.

Delivery is "at most once per interval": if a worker dies after claiming an endpoint, that
one check is skipped and the next one happens an interval later.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select, update

from app.core.checker import CheckOutcome, CheckSpec, run_check
from app.core.failure_detector import StateView, next_state
from app.db.models import AlertEvent, AlertState, CheckResult, Endpoint
from app.db.session import Database
from app.errors import NotFoundError
from app.notifications.base import AlertMessage, Notifier
from app.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedCheck:
    endpoint_id: int
    name: str
    spec: CheckSpec


@dataclass(frozen=True)
class Recorded:
    result: CheckResult
    status: str  # new state: unknown | healthy | unhealthy
    event: str | None  # "alert" | "recovery" when this check changed the state


def spec_for(endpoint: Endpoint) -> CheckSpec:
    return CheckSpec(
        url=endpoint.url, method=endpoint.method, expected_status=endpoint.expected_status,
        timeout_seconds=endpoint.timeout_seconds, latency_threshold_ms=endpoint.latency_threshold_ms,
    )


def claim_due_endpoints(db: Database, now: datetime, limit: int) -> list[ClaimedCheck]:
    with db.session() as session:
        rows = session.scalars(
            select(Endpoint)
            .where(Endpoint.enabled.is_(True), Endpoint.next_check_at <= now)
            .order_by(Endpoint.next_check_at, Endpoint.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        claimed = []
        for endpoint in rows:
            endpoint.next_check_at = now + timedelta(seconds=endpoint.interval_seconds)
            claimed.append(ClaimedCheck(endpoint.id, endpoint.name, spec_for(endpoint)))
        session.commit()
        return claimed


def record_outcome(db: Database, notifier: Notifier, endpoint_id: int, outcome: CheckOutcome,
                   checked_at: datetime | None = None) -> Recorded | None:
    """Store a result, update the endpoint's state, raise alerts. None if the endpoint is gone."""
    checked_at = checked_at or utcnow()
    message: AlertMessage | None = None
    event_id: int | None = None

    with db.session() as session:
        state = session.execute(
            select(AlertState).where(AlertState.endpoint_id == endpoint_id).with_for_update()
        ).scalar_one_or_none()
        endpoint = session.get(Endpoint, endpoint_id)
        if state is None or endpoint is None:
            return None  # deleted while the check was running

        result = CheckResult(
            endpoint_id=endpoint_id, checked_at=checked_at, status_code=outcome.status_code,
            latency_ms=outcome.latency_ms, success=outcome.success,
            error_type=outcome.error_type, error_message=outcome.error_message,
        )
        session.add(result)

        transition = next_state(StateView(state.status, state.consecutive_failures), outcome.success,
                                endpoint.failure_threshold)
        if transition.status != state.status:
            state.status_since = checked_at
        state.status = transition.status
        state.consecutive_failures = transition.consecutive_failures
        state.last_checked_at = checked_at
        state.last_success = outcome.success
        state.last_status_code = outcome.status_code
        state.last_latency_ms = outcome.latency_ms
        state.last_error_type = outcome.error_type

        if transition.event:
            detail = outcome.error_message or "Check failed"
            state.last_alert_at = checked_at if transition.event == "alert" else state.last_alert_at
            event = AlertEvent(
                endpoint_id=endpoint_id, kind=transition.event, created_at=checked_at,
                consecutive_failures=transition.consecutive_failures, message=detail[:255],
            )
            session.add(event)
            session.flush()
            event_id = event.id
            message = AlertMessage(
                kind=transition.event, endpoint_id=endpoint_id, endpoint_name=endpoint.name,
                url=endpoint.url, consecutive_failures=transition.consecutive_failures,
                detail=detail, occurred_at=checked_at,
            )
        session.commit()
        recorded = Recorded(result, transition.status, transition.event)

    if message is not None:  # only after the state change is safely committed
        _deliver(db, notifier, message, event_id)
    return recorded


def _deliver(db: Database, notifier: Notifier, message: AlertMessage, event_id: int | None) -> None:
    try:
        notifier.send(message)
    except Exception:  # noqa: BLE001 - a notification failure must never break monitoring
        logger.exception("Could not deliver %s notification for endpoint %s", message.kind, message.endpoint_id)
        return
    if event_id is not None:
        with db.session() as session:
            session.execute(update(AlertEvent).where(AlertEvent.id == event_id).values(delivered=True))
            session.commit()


def process_claimed(db: Database, notifier: Notifier, client: httpx.Client, claimed: ClaimedCheck) -> Recorded | None:
    outcome = run_check(claimed.spec, client)
    logger.info("check endpoint=%s name=%r success=%s status=%s latency_ms=%s error=%s",
                claimed.endpoint_id, claimed.name, outcome.success, outcome.status_code,
                outcome.latency_ms, outcome.error_type)
    return record_outcome(db, notifier, claimed.endpoint_id, outcome)


def check_endpoint_now(db: Database, notifier: Notifier, client: httpx.Client, endpoint_id: int) -> Recorded:
    """Manual check: same pipeline as a scheduled one (it counts towards failure detection)."""
    with db.session() as session:
        endpoint = session.get(Endpoint, endpoint_id)
        if endpoint is None:
            raise NotFoundError("Endpoint not found.")
        spec = spec_for(endpoint)
    outcome = run_check(spec, client)
    recorded = record_outcome(db, notifier, endpoint_id, outcome)
    if recorded is None:
        raise NotFoundError("Endpoint not found.")
    return recorded


def purge_old_results(db: Database, now: datetime, retention_days: int) -> int:
    cutoff = now - timedelta(days=retention_days)
    with db.session() as session:
        removed = session.execute(delete(CheckResult).where(CheckResult.checked_at < cutoff)).rowcount
        session.execute(delete(AlertEvent).where(AlertEvent.created_at < cutoff))
        session.commit()
    return removed or 0
