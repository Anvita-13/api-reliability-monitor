"""Users and sign-in."""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User
from app.errors import ServiceError
from app.security import DUMMY_HASH, hash_password, verify_password

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
MIN_PASSWORD_LENGTH = 8


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.username == username.strip().lower()))
    if user is None:
        verify_password(password, DUMMY_HASH)  # same cost whether or not the user exists
        return None
    if not verify_password(password, user.password_hash) or not user.is_active:
        return None
    return user


def create_user(session: Session, username: str, password: str, role: str = "viewer") -> User:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise ServiceError("Username must be 3-50 characters: letters, numbers, . _ -")
    if role not in ("admin", "viewer"):
        raise ServiceError("Role must be 'admin' or 'viewer'.")
    if len(password) < MIN_PASSWORD_LENGTH or password.isdigit() or password.isalpha():
        raise ServiceError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters and mix letters with numbers or symbols."
        )
    if session.scalar(select(User.id).where(User.username == username)):
        raise ServiceError("That username already exists.")
    user = User(username=username, password_hash=hash_password(password), role=role)
    session.add(user)
    session.commit()
    return user
