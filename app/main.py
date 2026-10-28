"""FastAPI application factory.

    uvicorn app.main:app --reload          # management API + dashboard
    python -m app.worker                   # the monitoring worker (separate process)
"""
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api.routes import auth, checks, endpoints, health, reports
from app.config import Settings, get_settings
from app.core.checker import build_client
from app.db.session import Database
from app.errors import ServiceError
from app.logging_setup import configure_logging
from app.metrics import build_metrics
from app.notifications import build_notifier
from app.security import RateLimiter
from app.storage import build_storage
from app.web.routes import RedirectToLogin
from app.web.routes import router as web_router
from app.worker.runner import Worker

APP_DIR = Path(__file__).resolve().parent
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = stop = thread = None
        if settings.run_worker_in_api:
            worker = Worker(app.state.db, app.state.notifier, settings, metrics=build_metrics(settings))
            stop = threading.Event()
            thread = threading.Thread(target=worker.run_forever, args=(stop,), name="worker", daemon=True)
            thread.start()
        yield
        if worker is not None:
            stop.set()
            thread.join(timeout=15)
            worker.close()
        app.state.http_client.close()
        app.state.db.dispose()

    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = Database(settings)
    app.state.notifier = build_notifier(settings)
    app.state.storage = build_storage(settings)
    app.state.http_client = build_client(settings.allow_private_targets)
    app.state.login_limiter = RateLimiter(limit=5, window_seconds=300)
    app.state.manual_check_limiter = RateLimiter(limit=10, window_seconds=60)

    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, session_cookie="monitor_session",
        max_age=settings.session_max_age_hours * 3600, same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    @app.middleware("http")
    async def protect(request: Request, call_next):
        # Defence in depth on top of SameSite=Lax cookies: a state-changing request that
        # names a different Origin is a cross-site request and is refused.
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin and urlsplit(origin).netloc != request.url.netloc:
            return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(RedirectToLogin)
    async def to_login(_request: Request, _exc: RedirectToLogin):
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/login", status_code=303)

    for router in (health.router, auth.router, endpoints.router, checks.router, reports.router, web_router):
        app.include_router(router)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    return app


app = create_app()
