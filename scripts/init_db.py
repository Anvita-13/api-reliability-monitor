"""Create the database tables, optionally with demo users and sample endpoints.

    python scripts/init_db.py                  # create the database (MySQL) and tables
    python scripts/init_db.py --seed           # ... plus demo users and sample endpoints
    python scripts/init_db.py --reset --seed   # drop all tables first (DESTROYS DATA), then rebuild

Connection details come from .env / environment variables (DB_HOST, DB_USER, ...).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.api.schemas.endpoint import EndpointCreate  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Endpoint, User  # noqa: E402
from app.db.session import Database, ensure_database  # noqa: E402
from app.services import auth_service, endpoint_service  # noqa: E402

DEMO_USERS = [("admin", "Admin@123", "admin"), ("viewer", "Viewer@123", "viewer")]
MOCK = "http://127.0.0.1:9100"
DEMO_ENDPOINTS = [
    dict(name="Example.com (public)", url="https://example.com", interval_seconds=60),
    dict(name="Mock API - always up", url=f"{MOCK}/ok", interval_seconds=15, timeout_seconds=5),
    dict(name="Mock API - switchable", url=f"{MOCK}/flaky", interval_seconds=15, timeout_seconds=5,
         failure_threshold=3),
    dict(name="Mock API - slow", url=f"{MOCK}/slow/800", interval_seconds=30, timeout_seconds=5,
         latency_threshold_ms=500),
]


def run(seed: bool = False, reset: bool = False) -> None:
    settings = get_settings()
    ensure_database(settings)
    db = Database(settings)
    if reset:
        db.drop_schema()
        print("Dropped all tables.")
    db.create_schema()
    print(f"Tables ready in database '{db.url.database}'.")
    if seed:
        with db.session() as session:
            for username, password, role in DEMO_USERS:
                if session.scalar(select(User.id).where(User.username == username)) is None:
                    auth_service.create_user(session, username, password, role)
                    print(f"  user {username} ({role})")
            if session.scalar(select(Endpoint.id).limit(1)) is None:
                for item in DEMO_ENDPOINTS:
                    endpoint_service.create_endpoint(session, EndpointCreate(**item), settings)
                    print(f"  endpoint {item['name']}")
        print("Demo data loaded. Demo logins: admin / Admin@123, viewer / Viewer@123")
    db.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", action="store_true", help="load demo users and sample endpoints")
    parser.add_argument("--reset", action="store_true", help="drop all tables first (destroys data)")
    args = parser.parse_args()
    run(seed=args.seed, reset=args.reset)
