"""Concurrency guarantees that depend on real row locking, so they run against MySQL only.

Skipped automatically when MySQL is not reachable. Uses the throwaway database api_monitor_test.
"""
import threading
from datetime import timedelta

import pytest

from app.core.checker import CheckOutcome
from app.db.models import AlertEvent, AlertState, CheckResult, Endpoint
from app.db.session import Database
from app.notifications.console import ConsoleNotifier
from app.services import monitoring_service
from app.timeutil import utcnow
from tests.conftest import mysql_settings


@pytest.fixture
def mysql_db(tmp_path):
    try:
        db = Database(mysql_settings(tmp_path))
        assert db.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"MySQL not available: {exc}")
    db.drop_schema()
    db.create_schema()
    yield db
    db.drop_schema()
    db.dispose()


def add_endpoints(db, count, threshold=3):
    with db.session() as s:
        for i in range(count):
            e = Endpoint(name=f"e{i}", url="http://127.0.0.1:9/", interval_seconds=60, timeout_seconds=2,
                         failure_threshold=threshold, next_check_at=utcnow() - timedelta(seconds=1))
            e.state = AlertState()
            s.add(e)
        s.commit()
        return [e.id for e in s.query(Endpoint).all()]


def test_concurrent_workers_never_claim_the_same_endpoint(mysql_db):
    ids = add_endpoints(mysql_db, 60)
    now, claimed, errors = utcnow(), [], []
    barrier = threading.Barrier(6)

    def worker():
        try:
            barrier.wait()
            claimed.append([c.endpoint_id for c in monitoring_service.claim_due_endpoints(mysql_db, now, limit=15)])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    flat = [i for batch in claimed for i in batch]
    assert len(flat) == len(set(flat)), "an endpoint was claimed by two workers at once"
    # a follow-up round picks up whatever the first round left, still without overlap
    rest = [c.endpoint_id for c in monitoring_service.claim_due_endpoints(mysql_db, now, limit=100)]
    assert sorted(flat + rest) == sorted(ids) and not set(flat) & set(rest)


def test_simultaneous_failures_raise_exactly_one_alert(mysql_db):
    (endpoint_id,) = add_endpoints(mysql_db, 1, threshold=3)
    notifier = ConsoleNotifier()
    barrier = threading.Barrier(12)
    errors = []

    def fail():
        try:
            barrier.wait()
            monitoring_service.record_outcome(mysql_db, notifier, endpoint_id,
                                              CheckOutcome(False, 500, 20, "unexpected_status", "boom"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=fail) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    with mysql_db.session() as s:
        state = s.get(AlertState, endpoint_id)
        assert state.consecutive_failures == 12 and state.status == "unhealthy"   # no lost updates
        assert s.query(CheckResult).count() == 12
        assert s.query(AlertEvent).filter_by(kind="alert").count() == 1           # ONE alert, not twelve
    assert len(notifier.sent) == 1


def test_simultaneous_recovery_raises_exactly_one_notice(mysql_db):
    (endpoint_id,) = add_endpoints(mysql_db, 1, threshold=1)
    notifier = ConsoleNotifier()
    monitoring_service.record_outcome(mysql_db, notifier, endpoint_id, CheckOutcome(False, 500, 5, "unexpected_status", "x"))
    barrier = threading.Barrier(8)

    def succeed():
        barrier.wait()
        monitoring_service.record_outcome(mysql_db, notifier, endpoint_id, CheckOutcome(True, 200, 5))

    threads = [threading.Thread(target=succeed) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert [m.kind for m in notifier.sent] == ["alert", "recovery"]


def test_mysql_enforces_the_data_rules(mysql_db):
    """The database itself, not just the app, refuses bad data."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError, OperationalError
    for sql in [
        "INSERT INTO endpoints (name,url,method,expected_status,timeout_seconds,interval_seconds,failure_threshold,enabled,next_check_at,created_at,updated_at) VALUES ('x','http://a','PUT',200,5,60,3,1,NOW(),NOW(),NOW())",
        "INSERT INTO endpoints (name,url,method,expected_status,timeout_seconds,interval_seconds,failure_threshold,enabled,next_check_at,created_at,updated_at) VALUES ('x','http://a','GET',700,5,60,3,1,NOW(),NOW(),NOW())",
        "INSERT INTO check_results (endpoint_id,checked_at,success) VALUES (999,NOW(),1)",
        "INSERT INTO users (username,password_hash,role,is_active,created_at) VALUES ('u','h','root',1,NOW())",
    ]:
        with mysql_db.session() as s:
            with pytest.raises((IntegrityError, OperationalError)):
                s.execute(text(sql))
                s.commit()
    (eid,) = add_endpoints(mysql_db, 1)
    with mysql_db.session() as s:
        s.execute(text("INSERT INTO check_results (endpoint_id,checked_at,success) VALUES (:e,NOW(),1)"), {"e": eid})
        s.commit()
        s.execute(text("DELETE FROM endpoints WHERE id=:e"), {"e": eid})   # ON DELETE CASCADE
        s.commit()
        assert s.query(CheckResult).count() == 0 and s.query(AlertState).count() == 0
