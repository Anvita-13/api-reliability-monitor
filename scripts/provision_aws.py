"""Prepare the database when an AWS server boots for the first time (run by the CloudFormation user data).

    python scripts/provision_aws.py

Safe to run on every boot:
  1. creates the application's MySQL user with rights on its own database only (and resets its
     password to the configured one),
  2. creates the tables,
  3. creates the first admin login if it does not exist,
  4. adds one public sample endpoint if there are no endpoints yet, so checks (and the
     "no checks recorded" alarm) have something to work with.

Reads the normal application settings (.env: DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_SSL_CA, ...)
plus DB_MASTER_USER, DB_MASTER_PASSWORD, ADMIN_USERNAME and ADMIN_PASSWORD from the environment.
The master credentials are only used here and are never written to .env.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.api.schemas.endpoint import EndpointCreate  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Endpoint, User  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.errors import ServiceError  # noqa: E402
from app.services import auth_service, endpoint_service  # noqa: E402

SAMPLE_ENDPOINT = dict(name="Example.com (sample)", url="https://example.com", interval_seconds=60)


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set")
    return value


def create_app_user(settings, master_user: str, master_password: str) -> None:
    for label, value in (("DB_NAME", settings.db_name), ("DB_USER", settings.db_user)):
        if not re.fullmatch(r"[A-Za-z0-9_]+", value):
            sys.exit(f"{label} may contain only letters, digits and underscores")
    conn = pymysql.connect(
        host=settings.db_host, port=settings.db_port, user=master_user, password=master_password,
        ssl={"ca": settings.db_ssl_ca} if settings.db_ssl_ca else None, autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` CHARACTER SET utf8mb4")
            cur.execute("CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s", (settings.db_user, settings.db_password))
            cur.execute("ALTER USER %s@'%%' IDENTIFIED BY %s", (settings.db_user, settings.db_password))
            cur.execute(f"GRANT ALL PRIVILEGES ON `{settings.db_name}`.* TO %s@'%%'", (settings.db_user,))
    finally:
        conn.close()
    print(f"Database user '{settings.db_user}' ready on '{settings.db_name}'.")


def main() -> None:
    settings = get_settings()
    create_app_user(settings, required("DB_MASTER_USER"), required("DB_MASTER_PASSWORD"))

    db = Database(settings)  # from here on: the application's own user
    try:
        db.create_schema()
        print("Tables ready.")
        username = required("ADMIN_USERNAME").strip().lower()
        with db.session() as session:
            if session.scalar(select(User.id).where(User.username == username)) is None:
                auth_service.create_user(session, username, required("ADMIN_PASSWORD"), "admin")
                print(f"Admin '{username}' created.")
            else:
                print(f"Admin '{username}' already exists.")
            if session.scalar(select(Endpoint.id).limit(1)) is None:
                endpoint_service.create_endpoint(session, EndpointCreate(**SAMPLE_ENDPOINT), settings)
                print(f"Sample endpoint '{SAMPLE_ENDPOINT['name']}' added.")
    except ServiceError as exc:
        sys.exit(exc.detail)
    finally:
        db.dispose()


if __name__ == "__main__":
    main()
