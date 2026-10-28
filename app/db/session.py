"""Engine and session management."""
import re
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Base


def _engine_options(url, settings: Settings) -> dict:
    options: dict = {"pool_pre_ping": True, "pool_recycle": 280}
    backend = url.get_backend_name()
    if backend == "mysql":
        # The worker claims rows with SELECT ... FOR UPDATE SKIP LOCKED; READ COMMITTED makes
        # the reads that follow a lock see the latest committed data.
        options["isolation_level"] = "READ COMMITTED"
        if settings.db_ssl_ca:
            options["connect_args"] = {"ssl": {"ca": settings.db_ssl_ca}}
    elif backend == "sqlite":
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return options


class Database:
    def __init__(self, settings: Settings):
        url = make_url(settings.sqlalchemy_url) if isinstance(settings.sqlalchemy_url, str) else settings.sqlalchemy_url
        self.url = url
        self.engine = create_engine(url, **_engine_options(url, settings))
        if url.get_backend_name() == "sqlite":
            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless asked
                cur.close()
        self.sessionmaker = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self):
        """A session that is always closed. Callers commit explicitly."""
        session: Session = self.sessionmaker()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create_schema(self):
        Base.metadata.create_all(self.engine)

    def drop_schema(self):
        Base.metadata.drop_all(self.engine)

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False

    def dispose(self):
        self.engine.dispose()


def ensure_database(settings: Settings) -> None:
    """MySQL only: create the database if it does not exist yet."""
    url = make_url(settings.sqlalchemy_url) if isinstance(settings.sqlalchemy_url, str) else settings.sqlalchemy_url
    if url.get_backend_name() != "mysql":
        return
    name = url.database or ""
    if not re.fullmatch(r"[A-Za-z0-9_$]+", name):
        raise ValueError("Invalid database name")
    engine = create_engine(url._replace(database=None), isolation_level="AUTOCOMMIT",
                           connect_args={"ssl": {"ca": settings.db_ssl_ca}} if settings.db_ssl_ca else {})
    try:
        with engine.connect() as conn:
            conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4"))
    finally:
        engine.dispose()
