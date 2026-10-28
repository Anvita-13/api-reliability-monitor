"""Server-rendered dashboard pages. All data changes go through the JSON API."""
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.api.deps import get_current_user, get_db
from app.db.models import CheckResult, User
from app.notifications.base import safe_url
from app.services import endpoint_service, reporting_service
from app.timeutil import utcnow

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


class RedirectToLogin(Exception):
    pass


def web_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise RedirectToLogin()
    return user


# ---- template filters ---------------------------------------------------------------

def _fmt_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "—"


def _ago(dt):
    if dt is None:
        return "never"
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    seconds = int((utcnow() - dt).total_seconds())
    if seconds < 0:
        return "in the future" if seconds < -5 else "just now"
    if seconds < 5:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _host(url: str) -> str:
    return urlsplit(url).netloc


templates.env.filters.update({"fmt_dt": _fmt_dt, "ago": _ago, "safe_url": safe_url, "host": _host})


def _render(request: Request, name: str, user: User | None, **context):
    return templates.TemplateResponse(
        request, name, {"user": user, "app_name": request.app.state.settings.app_name,
                        "version": __version__, **context})


def latency_bars(rows: list[CheckResult]) -> dict:
    """Inline-SVG data for a bar chart of the latest checks (oldest on the left)."""
    rows = list(reversed(rows))
    height, bar, gap = 60, 6, 2
    peak = max((r.latency_ms or 0 for r in rows), default=0) or 1
    bars = []
    for i, r in enumerate(rows):
        h = max(4, round(height * (r.latency_ms or 0) / peak)) if r.latency_ms is not None else height
        label = f"{_fmt_dt(r.checked_at)}: " + ("OK" if r.success else (r.error_type or "failed"))
        if r.latency_ms is not None:
            label += f", {r.latency_ms} ms"
        bars.append({"x": i * (bar + gap), "y": height - h, "h": h, "w": bar, "ok": r.success, "title": label})
    return {"bars": bars, "width": max(len(rows) * (bar + gap), 1), "height": height}


# ---- pages ---------------------------------------------------------------------------

@router.get("/")
def home():
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(get_current_user)):
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)
    return _render(request, "login.html", None)


@router.get("/dashboard")
def dashboard(request: Request, partial: bool = False, user: User = Depends(web_user),
              session: Session = Depends(get_db)):
    since = utcnow() - timedelta(hours=24)
    rows, _total = endpoint_service.list_endpoints(session, limit=500)
    context = {
        "endpoints": [endpoint_service.to_out(e) for e in rows],
        "availability": reporting_service.availability_by_endpoint(session, since),
        "overview": reporting_service.overview(session, since),
        "events": reporting_service.recent_events(session, limit=8),
        "defaults": request.app.state.settings,
    }
    return _render(request, "_live.html" if partial else "dashboard.html", user, **context)


@router.get("/dashboard/endpoints/{endpoint_id}")
def endpoint_page(endpoint_id: int, request: Request, partial: bool = False,
                  hours: int = Query(24, ge=1, le=720), user: User = Depends(web_user),
                  session: Session = Depends(get_db)):
    settings = request.app.state.settings
    endpoint = endpoint_service.to_out(endpoint_service.get_endpoint(session, endpoint_id))
    recent = session.scalars(
        select(CheckResult).where(CheckResult.endpoint_id == endpoint_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(60)
    ).all()
    context = {
        "endpoint": endpoint,
        "summary": reporting_service.endpoint_summary(session, endpoint_id, hours, settings),
        "hours": hours,
        "checks": recent[:25],
        "chart": latency_bars(recent),
        "events": reporting_service.recent_events(session, limit=10, endpoint_id=endpoint_id),
        "settings": settings,
    }
    return _render(request, "_endpoint_live.html" if partial else "endpoint_detail.html", user, **context)
