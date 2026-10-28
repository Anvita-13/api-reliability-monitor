from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_user
from app.api.schemas.auth import LoginIn, UserOut
from app.db.models import User
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _client_key(request: Request, username: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}|{username.strip().lower()}"


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, session: Session = Depends(get_db)):
    limiter = request.app.state.login_limiter
    key = _client_key(request, body.username)
    if limiter.blocked(key):
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Try again in a few minutes.")
    user = auth_service.authenticate(session, body.username, body.password)
    if user is None:
        limiter.record(key)
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    limiter.reset(key)
    request.session.clear()  # fresh session on login (prevents session fixation)
    request.session["user_id"] = user.id
    return user


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(require_user)):
    return user
