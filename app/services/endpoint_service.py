"""Create, read, update and delete monitored endpoints."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.schemas.endpoint import EndpointCreate, EndpointOut, EndpointUpdate, HealthOut
from app.config import Settings
from app.core.failure_detector import display_status
from app.core.url_guard import BlockedDestinationError, InvalidURLError, validate_target_url
from app.db.models import AlertState, Endpoint
from app.errors import NotFoundError, ValidationFailed
from app.timeutil import as_utc, utcnow


def _check_url(url: str, settings: Settings) -> str:
    try:
        return validate_target_url(url, allow_private=settings.allow_private_targets)
    except (InvalidURLError, BlockedDestinationError) as exc:
        raise ValidationFailed(str(exc)) from exc


def _check_limits(settings: Settings, interval: int | None, timeout: int | None):
    if interval is not None and interval < settings.min_interval_seconds:
        raise ValidationFailed(f"interval_seconds must be at least {settings.min_interval_seconds}.")
    if timeout is not None and timeout > settings.max_timeout_seconds:
        raise ValidationFailed(f"timeout_seconds must be at most {settings.max_timeout_seconds}.")


def get_endpoint(session: Session, endpoint_id: int) -> Endpoint:
    endpoint = session.get(Endpoint, endpoint_id)
    if endpoint is None:
        raise NotFoundError("Endpoint not found.")
    return endpoint


def create_endpoint(session: Session, data: EndpointCreate, settings: Settings) -> Endpoint:
    interval = data.interval_seconds or settings.default_interval_seconds
    timeout = data.timeout_seconds or settings.default_timeout_seconds
    _check_limits(settings, interval, timeout)
    if timeout >= interval:
        raise ValidationFailed("timeout_seconds must be shorter than interval_seconds.")
    endpoint = Endpoint(
        name=data.name,
        url=_check_url(data.url, settings),
        method=data.method,
        expected_status=data.expected_status,
        timeout_seconds=timeout,
        interval_seconds=interval,
        failure_threshold=data.failure_threshold or settings.default_failure_threshold,
        latency_threshold_ms=data.latency_threshold_ms,
        enabled=data.enabled,
        next_check_at=utcnow(),  # due immediately
    )
    endpoint.state = AlertState()
    session.add(endpoint)
    session.commit()
    return endpoint


def update_endpoint(session: Session, endpoint_id: int, patch: EndpointUpdate, settings: Settings) -> Endpoint:
    endpoint = get_endpoint(session, endpoint_id)
    changes = patch.model_dump(exclude_unset=True)
    clear_latency = changes.pop("clear_latency_threshold", False)
    _check_limits(settings, changes.get("interval_seconds"), changes.get("timeout_seconds"))
    if "url" in changes:
        changes["url"] = _check_url(changes["url"], settings)
    for key in ("name", "method", "expected_status", "enabled", "failure_threshold", "url"):
        if changes.get(key) is not None:
            setattr(endpoint, key, changes[key])
    if clear_latency:
        endpoint.latency_threshold_ms = None
    elif changes.get("latency_threshold_ms") is not None:
        endpoint.latency_threshold_ms = changes["latency_threshold_ms"]

    now = utcnow()
    if changes.get("interval_seconds") is not None:
        endpoint.interval_seconds = changes["interval_seconds"]
        # a shorter interval takes effect right away instead of after the old wait
        endpoint.next_check_at = min(endpoint.next_check_at, now + timedelta(seconds=endpoint.interval_seconds))
    if changes.get("timeout_seconds") is not None:
        endpoint.timeout_seconds = changes["timeout_seconds"]
    if endpoint.timeout_seconds >= endpoint.interval_seconds:
        session.rollback()
        raise ValidationFailed("timeout_seconds must be shorter than interval_seconds.")
    if changes.get("enabled") is True:
        endpoint.next_check_at = now  # re-enabled: check soon
    session.commit()
    return endpoint


def delete_endpoint(session: Session, endpoint_id: int) -> None:
    endpoint = get_endpoint(session, endpoint_id)
    session.delete(endpoint)  # results, state and events go with it (ON DELETE CASCADE)
    session.commit()


def list_endpoints(session: Session, enabled: bool | None = None, limit: int = 100, offset: int = 0):
    stmt = select(Endpoint).options(selectinload(Endpoint.state))
    count = select(func.count()).select_from(Endpoint)
    if enabled is not None:
        stmt, count = stmt.where(Endpoint.enabled == enabled), count.where(Endpoint.enabled == enabled)
    total = session.scalar(count)
    rows = session.scalars(stmt.order_by(Endpoint.name, Endpoint.id).limit(limit).offset(offset)).all()
    return rows, total


def endpoint_health(endpoint: Endpoint) -> HealthOut:
    s = endpoint.state or AlertState()
    return HealthOut(
        status=display_status(s.status or "unknown", s.consecutive_failures or 0, endpoint.enabled),
        consecutive_failures=s.consecutive_failures or 0,
        status_since=as_utc(s.status_since),
        last_checked_at=as_utc(s.last_checked_at),
        last_success=s.last_success,
        last_status_code=s.last_status_code,
        last_latency_ms=s.last_latency_ms,
        last_error_type=s.last_error_type,
    )


def to_out(endpoint: Endpoint) -> EndpointOut:
    return EndpointOut(
        id=endpoint.id, name=endpoint.name, url=endpoint.url, method=endpoint.method,
        expected_status=endpoint.expected_status, timeout_seconds=endpoint.timeout_seconds,
        interval_seconds=endpoint.interval_seconds, failure_threshold=endpoint.failure_threshold,
        latency_threshold_ms=endpoint.latency_threshold_ms, enabled=endpoint.enabled,
        created_at=as_utc(endpoint.created_at), next_check_at=as_utc(endpoint.next_check_at),
        health=endpoint_health(endpoint),
    )
