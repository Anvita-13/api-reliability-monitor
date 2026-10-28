from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin, require_user
from app.api.schemas.endpoint import EndpointCreate, EndpointListOut, EndpointOut, EndpointUpdate
from app.services import endpoint_service

router = APIRouter(prefix="/endpoints", tags=["endpoints"])


@router.post("", response_model=EndpointOut, status_code=201, dependencies=[Depends(require_admin)])
def register_endpoint(body: EndpointCreate, request: Request, session: Session = Depends(get_db)):
    endpoint = endpoint_service.create_endpoint(session, body, request.app.state.settings)
    return endpoint_service.to_out(endpoint)


@router.get("", response_model=EndpointListOut, dependencies=[Depends(require_user)])
def list_endpoints(
    enabled: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
):
    rows, total = endpoint_service.list_endpoints(session, enabled, limit, offset)
    return EndpointListOut(items=[endpoint_service.to_out(e) for e in rows], total=total, limit=limit, offset=offset)


@router.get("/{endpoint_id}", response_model=EndpointOut, dependencies=[Depends(require_user)])
def get_endpoint(endpoint_id: int, session: Session = Depends(get_db)):
    return endpoint_service.to_out(endpoint_service.get_endpoint(session, endpoint_id))


@router.patch("/{endpoint_id}", response_model=EndpointOut, dependencies=[Depends(require_admin)])
def update_endpoint(endpoint_id: int, body: EndpointUpdate, request: Request, session: Session = Depends(get_db)):
    endpoint = endpoint_service.update_endpoint(session, endpoint_id, body, request.app.state.settings)
    return endpoint_service.to_out(endpoint)


@router.delete("/{endpoint_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_endpoint(endpoint_id: int, session: Session = Depends(get_db)):
    endpoint_service.delete_endpoint(session, endpoint_id)
    return Response(status_code=204)
