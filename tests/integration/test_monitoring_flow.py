"""The whole monitoring workflow: schedule -> check -> record -> detect failures -> alert -> recover."""
from datetime import timedelta
from unittest.mock import MagicMock

from app.core.checker import CheckOutcome
from app.db.models import AlertEvent, AlertState, CheckResult
from app.services import monitoring_service
from app.timeutil import utcnow
from tests.conftest import new_endpoint

INTERVAL = 10


def tick(worker, t0, n):
    """Advance the worker's clock to t0 + n*(interval+1) seconds and run it once."""
    return worker.run_once(now=t0 + timedelta(seconds=n * (INTERVAL + 1)))


def health(admin, ep):
    return admin.get(f"/endpoints/{ep['id']}").json()["health"]


def test_first_check_runs_immediately_and_is_recorded(admin, worker, mock):
    ep = new_endpoint(admin, mock.url("/ok"))
    assert worker.run_once() == 1
    h = health(admin, ep)
    assert h["status"] == "healthy" and h["last_success"] is True and h["last_status_code"] == 200
    assert h["last_latency_ms"] is not None and h["last_checked_at"] is not None
    checks = admin.get(f"/endpoints/{ep['id']}/checks").json()
    assert checks["total"] == 1 and checks["items"][0]["success"] is True


def test_worker_reports_how_many_checks_it_recorded(admin, worker, mock):
    worker.metrics = MagicMock()
    new_endpoint(admin, mock.url("/ok"))
    new_endpoint(admin, mock.url("/ok"), name="second")
    assert worker.run_once() == 2
    worker.metrics.checks_processed.assert_called_once_with(2)
    worker.metrics.reset_mock()
    assert worker.run_once() == 0                                    # nothing due: nothing reported
    worker.metrics.checks_processed.assert_not_called()


def test_endpoint_is_only_checked_when_due(admin, worker, mock):
    new_endpoint(admin, mock.url("/ok"))
    t0 = utcnow()
    assert worker.run_once(now=t0) == 1
    assert worker.run_once(now=t0 + timedelta(seconds=3)) == 0      # not due yet
    assert worker.run_once(now=t0 + timedelta(seconds=INTERVAL - 1)) == 0
    assert worker.run_once(now=t0 + timedelta(seconds=INTERVAL + 1)) == 1
    assert mock.hits["/ok"] == 2


def test_claiming_is_exclusive_so_nothing_is_checked_twice(admin, db, mock):
    for i in range(5):
        new_endpoint(admin, mock.url("/ok"), name=f"e{i}")
    now = utcnow() + timedelta(seconds=1)
    first = monitoring_service.claim_due_endpoints(db, now, limit=100)
    second = monitoring_service.claim_due_endpoints(db, now, limit=100)
    assert len(first) == 5 and second == []


def test_disabled_endpoints_are_never_checked(admin, worker, mock):
    ep = new_endpoint(admin, mock.url("/ok"), enabled=False)
    assert worker.run_once(now=utcnow() + timedelta(hours=1)) == 0
    assert mock.hits.get("/ok", 0) == 0 and health(admin, ep)["status"] == "disabled"


def test_outage_alerts_exactly_once_and_recovery_exactly_once(admin, worker, app, db, mock):
    ep = new_endpoint(admin, mock.url("/flaky"), failure_threshold=3)
    notifier, t0 = app.state.notifier, utcnow()

    tick(worker, t0, 0)                                    # healthy
    assert health(admin, ep)["status"] == "healthy"

    mock.mode = "down"
    tick(worker, t0, 1)                                    # 1st failure
    assert health(admin, ep)["status"] == "degraded" and health(admin, ep)["consecutive_failures"] == 1
    tick(worker, t0, 2)                                    # 2nd failure
    assert health(admin, ep)["status"] == "degraded" and notifier.sent == []   # below the threshold: no alert
    tick(worker, t0, 3)                                    # 3rd failure -> alert
    assert health(admin, ep)["status"] == "unhealthy"
    assert [m.kind for m in notifier.sent] == ["alert"]
    assert notifier.sent[0].consecutive_failures == 3 and "500" in notifier.sent[0].detail

    for n in range(4, 9):                                  # the outage continues: NO further alerts
        tick(worker, t0, n)
    assert health(admin, ep)["consecutive_failures"] == 8
    assert [m.kind for m in notifier.sent] == ["alert"]

    mock.mode = "up"
    tick(worker, t0, 9)                                    # recovery
    assert health(admin, ep)["status"] == "healthy" and health(admin, ep)["consecutive_failures"] == 0
    assert [m.kind for m in notifier.sent] == ["alert", "recovery"]
    tick(worker, t0, 10)
    assert [m.kind for m in notifier.sent] == ["alert", "recovery"]   # healthy stays quiet

    with db.session() as s:
        events = s.query(AlertEvent).order_by(AlertEvent.id).all()
        assert [(e.kind, e.delivered) for e in events] == [("alert", True), ("recovery", True)]
        state = s.get(AlertState, ep["id"])
        assert state.last_alert_at is not None and state.status == "healthy"


def test_a_second_outage_raises_a_new_alert(admin, worker, app, mock):
    ep = new_endpoint(admin, mock.url("/flaky"), failure_threshold=2)
    t0, n = utcnow(), 0
    for mode in ("down", "down", "up", "down", "down", "up"):
        mock.mode = mode
        tick(worker, t0, n); n += 1
    assert [m.kind for m in app.state.notifier.sent] == ["alert", "recovery", "alert", "recovery"]
    assert health(admin, ep)["status"] == "healthy"


def test_history_summary_and_filters(admin, viewer, worker, mock):
    ep = new_endpoint(admin, mock.url("/flaky"), failure_threshold=3)
    t0, n = utcnow(), 0
    for mode in ["up"] * 6 + ["down"] * 3 + ["up"]:        # 10 checks: 7 pass, 3 fail
        mock.mode = mode
        tick(worker, t0, n); n += 1

    history = admin.get(f"/endpoints/{ep['id']}/checks?limit=100").json()
    assert history["total"] == 10
    times = [c["checked_at"] for c in history["items"]]
    assert times == sorted(times, reverse=True)           # newest first
    assert admin.get(f"/endpoints/{ep['id']}/checks?success=false").json()["total"] == 3
    assert admin.get(f"/endpoints/{ep['id']}/checks?success=true").json()["total"] == 7
    page = admin.get(f"/endpoints/{ep['id']}/checks?limit=4&offset=8").json()
    assert len(page["items"]) == 2 and page["total"] == 10
    failed = admin.get(f"/endpoints/{ep['id']}/checks?success=false").json()["items"][0]
    assert failed["status_code"] == 500 and failed["error_type"] == "unexpected_status"

    s = viewer.get(f"/endpoints/{ep['id']}/summary?hours=1").json()
    assert (s["total_checks"], s["successful_checks"], s["failed_checks"]) == (10, 7, 3)
    assert s["availability_pct"] == 70.0 and s["incidents"] == 1
    assert s["min_latency_ms"] <= s["avg_latency_ms"] <= s["max_latency_ms"] and s["min_latency_ms"] <= s["p95_latency_ms"] <= s["max_latency_ms"]
    assert s["current_status"] == "healthy" and sum(p["checks"] for p in s["trend"]) == 10
    assert admin.get(f"/endpoints/{ep['id']}/summary?hours=0").status_code == 422
    assert admin.get(f"/endpoints/{ep['id']}/summary?hours=721").status_code == 422


def test_timeout_is_recorded_with_its_error_type(admin, worker, mock):
    mock.hang_seconds = 3
    ep = new_endpoint(admin, mock.url("/flaky"), timeout_seconds=1)
    mock.mode = "hang"
    worker.run_once()
    c = admin.get(f"/endpoints/{ep['id']}/checks").json()["items"][0]
    assert c["success"] is False and c["error_type"] == "timeout" and c["status_code"] is None
    assert 900 <= c["latency_ms"] < 2500


def test_unreachable_host_is_recorded(admin, worker, closed_port):
    ep = new_endpoint(admin, f"http://127.0.0.1:{closed_port}/", timeout_seconds=8, interval_seconds=30)
    worker.run_once()
    c = admin.get(f"/endpoints/{ep['id']}/checks").json()["items"][0]
    assert c["success"] is False and c["error_type"] == "connection_error"


def test_slow_response_threshold_counts_as_a_failure(admin, worker, mock):
    ep = new_endpoint(admin, mock.url("/slow/400"), latency_threshold_ms=100, failure_threshold=1)
    worker.run_once()
    c = admin.get(f"/endpoints/{ep['id']}/checks").json()["items"][0]
    assert c["error_type"] == "slow_response" and c["status_code"] == 200 and not c["success"]
    assert health(admin, ep)["status"] == "unhealthy"


def test_results_stay_attached_to_the_right_endpoint(admin, worker, mock):
    good = new_endpoint(admin, mock.url("/ok"), name="good")
    bad = new_endpoint(admin, mock.url("/status/503"), name="bad", failure_threshold=1)
    other = new_endpoint(admin, mock.url("/status/404"), name="other", expected_status=404)
    assert worker.run_once() == 3
    assert health(admin, good)["status"] == "healthy"
    assert health(admin, bad)["status"] == "unhealthy" and health(admin, bad)["last_status_code"] == 503
    assert health(admin, other)["status"] == "healthy" and health(admin, other)["last_status_code"] == 404
    for ep in (good, bad, other):
        assert admin.get(f"/endpoints/{ep['id']}/checks").json()["total"] == 1


def test_one_broken_endpoint_does_not_stop_the_others(admin, worker, mock, closed_port):
    new_endpoint(admin, f"http://127.0.0.1:{closed_port}/", name="dead", timeout_seconds=8, interval_seconds=30)
    ok = new_endpoint(admin, mock.url("/ok"), name="alive")
    assert worker.run_once() == 2
    assert health(admin, ok)["status"] == "healthy"


def test_manual_check_records_a_result_and_can_trigger_an_alert(admin, app, mock):
    ep = new_endpoint(admin, mock.url("/flaky"), failure_threshold=1)
    ok = admin.post(f"/endpoints/{ep['id']}/check")
    assert ok.status_code == 200 and ok.json()["check"]["success"] is True and ok.json()["event"] is None
    mock.mode = "down"
    r = admin.post(f"/endpoints/{ep['id']}/check").json()
    assert r["check"]["success"] is False and r["event"] == "alert" and r["status"] == "unhealthy"
    assert [m.kind for m in app.state.notifier.sent] == ["alert"]
    assert admin.get(f"/endpoints/{ep['id']}/checks").json()["total"] == 2


def test_manual_checks_are_rate_limited(admin, app, mock):
    from app.security import RateLimiter
    app.state.manual_check_limiter = RateLimiter(limit=2, window_seconds=60)
    ep = new_endpoint(admin, mock.url("/ok"))
    codes = [admin.post(f"/endpoints/{ep['id']}/check").status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]


def test_a_failing_notification_never_breaks_monitoring(admin, worker, app, db, mock):
    app.state.notifier = worker.notifier = MagicMock()
    app.state.notifier.send.side_effect = RuntimeError("SNS is down")
    ep = new_endpoint(admin, mock.url("/status/500"), failure_threshold=1)
    worker.run_once()
    assert health(admin, ep)["status"] == "unhealthy"           # state still updated
    with db.session() as s:
        e = s.query(AlertEvent).one()
        assert e.kind == "alert" and e.delivered is False        # recorded as not delivered


def test_a_result_for_a_deleted_endpoint_is_dropped_quietly(db, app):
    outcome = CheckOutcome(False, 500, 10, "unexpected_status", "x")
    assert monitoring_service.record_outcome(db, app.state.notifier, 12345, outcome) is None


def test_old_results_are_purged(admin, worker, db, mock):
    ep = new_endpoint(admin, mock.url("/ok"))
    worker.run_once()
    with db.session() as s:
        s.add(CheckResult(endpoint_id=ep["id"], checked_at=utcnow() - timedelta(days=45), success=True, status_code=200, latency_ms=5))
        s.add(AlertEvent(endpoint_id=ep["id"], kind="alert", created_at=utcnow() - timedelta(days=45), message="old"))
        s.commit()
    assert monitoring_service.purge_old_results(db, utcnow(), retention_days=30) == 1
    with db.session() as s:
        assert s.query(CheckResult).count() == 1 and s.query(AlertEvent).count() == 0


def test_manual_and_scheduled_checks_share_one_state(admin, worker, mock):
    ep = new_endpoint(admin, mock.url("/flaky"), failure_threshold=3)
    mock.mode = "down"
    t0 = utcnow()
    admin.post(f"/endpoints/{ep['id']}/check")        # failure 1
    tick(worker, t0, 0)                                # failure 2
    assert health(admin, ep)["consecutive_failures"] == 2
    admin.post(f"/endpoints/{ep['id']}/check")        # failure 3 -> alert
    assert health(admin, ep)["status"] == "unhealthy"


def test_worker_run_forever_picks_up_new_endpoints_and_stops_cleanly(admin, worker, mock):
    import threading
    import time
    stop = threading.Event()
    t = threading.Thread(target=worker.run_forever, args=(stop,))
    t.start()
    ep = new_endpoint(admin, mock.url("/ok"))
    deadline = time.time() + 5
    while time.time() < deadline and health(admin, ep)["status"] == "unknown":
        time.sleep(0.05)
    stop.set(); t.join(timeout=5)
    assert not t.is_alive() and health(admin, ep)["status"] == "healthy"
