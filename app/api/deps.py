"""FastAPI dependencies: database session, current user, role checks."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db.models import User


def get_db(request: Request):
    session: Session = request.app.state.db.sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_current_user(request: Request, session: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        return None
    return user


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user
