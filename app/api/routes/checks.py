from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin, require_user
from app.api.schemas.monitoring import CheckListOut, ManualCheckOut, SummaryOut
from app.db.models import User
from app.services import monitoring_service, reporting_service
from app.services.endpoint_service import get_endpoint

router = APIRouter(prefix="/endpoints", tags=["checks"])


@router.get("/{endpoint_id}/checks", response_model=CheckListOut, dependencies=[Depends(require_user)])
def check_history(
    endpoint_id: int,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    success: bool | None = None,
    since: datetime | None = Query(None, description="Only checks at or after this time (ISO 8601, UTC)"),
    session: Session = Depends(get_db),
):
    if since is not None and since.tzinfo is not None:
        since = since.astimezone(UTC).replace(tzinfo=None)
    rows, total = reporting_service.list_checks(session, endpoint_id, limit, offset, success, since)
    return CheckListOut(items=[reporting_service.check_to_out(r) for r in rows], total=total,
                        limit=limit, offset=offset)


@router.get("/{endpoint_id}/summary", response_model=SummaryOut, dependencies=[Depends(require_user)])
def summary(endpoint_id: int, request: Request, hours: int = Query(24, ge=1, le=720),
            session: Session = Depends(get_db)):
    return reporting_service.endpoint_summary(session, endpoint_id, hours, request.app.state.settings)


@router.post("/{endpoint_id}/check", response_model=ManualCheckOut)
def manual_check(endpoint_id: int, request: Request, user: User = Depends(require_admin),
                 session: Session = Depends(get_db)):
    """Run a health check right now. Rate-limited per user."""
    get_endpoint(session, endpoint_id)  # 404 before spending a rate-limit slot
    if not request.app.state.manual_check_limiter.allow(f"user:{user.id}"):
        raise HTTPException(status_code=429, detail="Too many manual checks. Please wait a moment.")
    state = request.app.state
    recorded = monitoring_service.check_endpoint_now(state.db, state.notifier, state.http_client, endpoint_id)
    return ManualCheckOut(check=reporting_service.check_to_out(recorded.result),
                          status=recorded.status, event=recorded.event)
