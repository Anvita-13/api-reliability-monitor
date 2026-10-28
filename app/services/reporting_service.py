"""Check history, availability summaries and exported reports."""
import csv
import io
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.api.schemas.monitoring import CheckOut, ExportOut, SummaryOut
from app.config import Settings
from app.core.metrics import Row, summarize
from app.db.models import AlertEvent, AlertState, CheckResult, Endpoint
from app.services.endpoint_service import endpoint_health, get_endpoint
from app.storage import Storage
from app.timeutil import as_utc, iso_z, utcnow


def check_to_out(r: CheckResult) -> CheckOut:
    return CheckOut(
        id=r.id, endpoint_id=r.endpoint_id, checked_at=as_utc(r.checked_at), status_code=r.status_code,
        latency_ms=r.latency_ms, success=r.success, error_type=r.error_type, error_message=r.error_message,
    )


def list_checks(session: Session, endpoint_id: int, limit: int, offset: int,
                success: bool | None = None, since: datetime | None = None):
    get_endpoint(session, endpoint_id)  # 404 if it does not exist
    conditions = [CheckResult.endpoint_id == endpoint_id]
    if success is not None:
        conditions.append(CheckResult.success == success)
    if since is not None:
        conditions.append(CheckResult.checked_at >= since)
    total = session.scalar(select(func.count()).select_from(CheckResult).where(*conditions))
    rows = session.scalars(
        select(CheckResult).where(*conditions)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(limit).offset(offset)
    ).all()
    return rows, total


def endpoint_summary(session: Session, endpoint_id: int, hours: int, settings: Settings) -> SummaryOut:
    endpoint = get_endpoint(session, endpoint_id)
    until = utcnow()
    since = until - timedelta(hours=hours)
    cap = settings.max_summary_rows
    rows = session.execute(
        select(CheckResult.checked_at, CheckResult.success, CheckResult.status_code, CheckResult.latency_ms)
        .where(CheckResult.endpoint_id == endpoint_id, CheckResult.checked_at >= since)
        .order_by(CheckResult.checked_at.desc()).limit(cap + 1)
    ).all()
    truncated = len(rows) > cap
    data = summarize([Row(*r) for r in rows[:cap]], since, until)
    incidents = session.scalar(
        select(func.count()).select_from(AlertEvent)
        .where(AlertEvent.endpoint_id == endpoint_id, AlertEvent.kind == "alert", AlertEvent.created_at >= since)
    )
    trend = [{**p, "start": as_utc(p["start"])} for p in data.pop("trend")]
    return SummaryOut(
        endpoint_id=endpoint_id, hours=hours, since=as_utc(since), until=as_utc(until),
        current_status=endpoint_health(endpoint).status, incidents=incidents or 0,
        truncated=truncated, trend=trend, **data,
    )


def recent_events(session: Session, limit: int = 10, endpoint_id: int | None = None):
    stmt = select(AlertEvent, Endpoint.name).join(Endpoint, Endpoint.id == AlertEvent.endpoint_id)
    if endpoint_id is not None:
        stmt = stmt.where(AlertEvent.endpoint_id == endpoint_id)
    return session.execute(stmt.order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc()).limit(limit)).all()


def availability_by_endpoint(session: Session, since: datetime) -> dict[int, float | None]:
    """24h-style availability for every endpoint with one grouped query."""
    rows = session.execute(
        select(CheckResult.endpoint_id, func.count(), func.sum(CheckResult.success.cast(Integer)))
        .where(CheckResult.checked_at >= since).group_by(CheckResult.endpoint_id)
    ).all()
    return {eid: round(100.0 * int(ok or 0) / total, 2) for eid, total, ok in rows if total}


def overview(session: Session, since: datetime) -> dict:
    """Numbers for the dashboard header cards."""
    counts = dict(session.execute(
        select(AlertState.status, func.count()).join(Endpoint, Endpoint.id == AlertState.endpoint_id)
        .where(Endpoint.enabled.is_(True)).group_by(AlertState.status)
    ).all())
    degraded = session.scalar(
        select(func.count()).select_from(AlertState).join(Endpoint, Endpoint.id == AlertState.endpoint_id)
        .where(Endpoint.enabled.is_(True), AlertState.status != "unhealthy", AlertState.consecutive_failures > 0)
    ) or 0
    total_checks, ok_checks = session.execute(
        select(func.count(), func.coalesce(func.sum(CheckResult.success.cast(Integer)), 0))
        .where(CheckResult.checked_at >= since)
    ).one()
    return {
        "total": session.scalar(select(func.count()).select_from(Endpoint)) or 0,
        "unhealthy": counts.get("unhealthy", 0),
        "degraded": degraded,
        "healthy": max(counts.get("healthy", 0) - degraded, 0),
        "unknown": counts.get("unknown", 0),
        "availability_pct": round(100.0 * int(ok_checks) / total_checks, 2) if total_checks else None,
        "checks_24h": total_checks,
    }


_EXPORT_COLUMNS = ["check_id", "endpoint_id", "endpoint_name", "checked_at", "status_code",
                   "latency_ms", "success", "error_type", "error_message"]


def _csv_safe(value) -> str:
    """Neutralise spreadsheet formula injection (error text can come from a remote server)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def export_report(session: Session, storage: Storage, settings: Settings, hours: int,
                  fmt: str, endpoint_id: int | None = None) -> ExportOut:
    """Write check results for a period to storage (local folder, or S3 on AWS)."""
    if endpoint_id is not None:
        get_endpoint(session, endpoint_id)
    since = utcnow() - timedelta(hours=hours)
    stmt = (
        select(CheckResult, Endpoint.name).join(Endpoint, Endpoint.id == CheckResult.endpoint_id)
        .where(CheckResult.checked_at >= since)
    )
    if endpoint_id is not None:
        stmt = stmt.where(CheckResult.endpoint_id == endpoint_id)
    records = session.execute(
        stmt.order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(settings.max_summary_rows)
    ).all()

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_EXPORT_COLUMNS)
        for r, name in records:
            writer.writerow([r.id, r.endpoint_id, _csv_safe(name), iso_z(r.checked_at), r.status_code,
                             r.latency_ms, str(r.success).lower(), r.error_type or "", _csv_safe(r.error_message)])
        data, content_type = ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv"
    else:
        payload = [
            {"check_id": r.id, "endpoint_id": r.endpoint_id, "endpoint_name": name,
             "checked_at": iso_z(r.checked_at), "status_code": r.status_code, "latency_ms": r.latency_ms,
             "success": r.success, "error_type": r.error_type, "error_message": r.error_message}
            for r, name in records
        ]
        data, content_type = json.dumps(payload, indent=2).encode("utf-8"), "application/json"

    name = f"{utcnow():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.{fmt}"
    storage.save(f"exports/{name}", data, content_type)
    return ExportOut(key=f"exports/{name}", rows=len(records), size_bytes=len(data),
                     download_url=f"/reports/download/{name}")
