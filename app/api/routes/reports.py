import re

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin, require_user
from app.api.schemas.monitoring import ExportOut, ExportRequest
from app.services import reporting_service

router = APIRouter(prefix="/reports", tags=["reports"])

NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@router.post("/export", response_model=ExportOut, status_code=201, dependencies=[Depends(require_admin)])
def export(body: ExportRequest, request: Request, session: Session = Depends(get_db)):
    """Write check history for a period to storage (local folder; S3 on AWS)."""
    state = request.app.state
    return reporting_service.export_report(session, state.storage, state.settings, body.hours,
                                           body.format, body.endpoint_id)


@router.get("/download/{name}", dependencies=[Depends(require_user)])
def download(name: str, request: Request):
    if not NAME_RE.match(name):
        raise HTTPException(status_code=404, detail="Report not found.")
    return request.app.state.storage.response(f"exports/{name}", download_name=name)
