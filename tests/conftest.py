"""Shared fixtures.

The integration tests use SQLite by default (fast, no setup). To run them against MySQL,
which is what production uses:

    TEST_DB=mysql pytest -v          # uses database "api_monitor_test" with the credentials in .env
"""
import os
import socket

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.checker import build_client
from app.db.models import User
from app.db.session import Database, ensure_database
from app.main import create_app
from app.notifications.console import ConsoleNotifier
from app.security import hash_password
from app.worker.runner import Worker
from tests.mock_server import MockServer

TEST_DB = os.environ.get("TEST_DB", "sqlite").lower()
PASSWORD = "Passw0rd!x"


def mysql_settings(tmp_path, **extra) -> Settings:
    """Settings pointing at the throwaway MySQL database api_monitor_test."""
    base = Settings()  # reads credentials from .env / environment
    s = Settings(_env_file=None, secret_key="test-secret", allow_private_targets=True, min_interval_seconds=1,
                 db_host=base.db_host, db_port=base.db_port, db_user=base.db_user,
                 db_password=base.db_password, db_name="api_monitor_test",
                 exports_dir=str(tmp_path / "exports"), **extra)
    ensure_database(s)
    return s


@pytest.fixture(scope="session")
def password_hash() -> str:
    # Production uses 600k PBKDF2 iterations. Tests store a cheap hash (verify_password reads the
    # iteration count from the stored value), so logging in does not dominate the run time.
    return hash_password(PASSWORD, iterations=1000)


@pytest.fixture(scope="session")
def mock_server():
    server = MockServer(hang_seconds=3.0).start()
    yield server
    server.stop()


@pytest.fixture
def mock(mock_server):
    mock_server.mode = "up"
    mock_server.hits.clear()
    return mock_server


@pytest.fixture
def settings(tmp_path) -> Settings:
    common = dict(_env_file=None, secret_key="test-secret", allow_private_targets=True,
                  min_interval_seconds=1, exports_dir=str(tmp_path / "exports"), worker_poll_seconds=0.05)
    if TEST_DB == "mysql":
        return mysql_settings(tmp_path, worker_poll_seconds=0.05)
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", **common)


@pytest.fixture
def app(settings):
    application = create_app(settings)
    application.state.db.drop_schema()
    application.state.db.create_schema()
    application.state.notifier = ConsoleNotifier()
    yield application
    application.state.http_client.close()
    application.state.db.drop_schema()
    application.state.db.dispose()


@pytest.fixture
def db(app) -> Database:
    return app.state.db


def _make_client(app, db, password_hash, username, role) -> TestClient:
    with db.session() as s:
        s.add(User(username=username, password_hash=password_hash, role=role))
        s.commit()
    client = TestClient(app)
    response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def admin(app, db, password_hash) -> TestClient:
    return _make_client(app, db, password_hash, "admin", "admin")


@pytest.fixture
def viewer(app, db, password_hash) -> TestClient:
    return _make_client(app, db, password_hash, "viewer", "viewer")


@pytest.fixture
def anon(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def worker(app, settings):
    w = Worker(app.state.db, app.state.notifier, settings, client=build_client(allow_private=True))
    yield w
    w.close()


@pytest.fixture
def closed_port() -> int:
    """A local TCP port with nothing listening (connection refused)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def new_endpoint(client: TestClient, url: str, **overrides) -> dict:
    body = {"name": "test api", "url": url, "interval_seconds": 10, "timeout_seconds": 2,
            "failure_threshold": 3, **overrides}
    response = client.post("/endpoints", json=body)
    assert response.status_code == 201, response.text
    return response.json()
