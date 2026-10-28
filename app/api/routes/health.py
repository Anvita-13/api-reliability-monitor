from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request):
    """Liveness/readiness probe (an Application Load Balancer target group uses this).

    Returns 200 only if the database answers.
    """
    if request.app.state.db.ping():
        return {"status": "ok", "database": "ok"}
    return JSONResponse({"status": "unhealthy", "database": "unreachable"}, status_code=503)
