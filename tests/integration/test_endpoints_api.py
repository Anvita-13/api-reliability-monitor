import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import RateLimiter
from tests.conftest import new_endpoint

# ---- authentication & roles ------------------------------------------------------------

def test_api_requires_login(anon):
    for method, path in [("get", "/endpoints"), ("post", "/endpoints"), ("get", "/endpoints/1"),
                         ("get", "/endpoints/1/checks"), ("get", "/endpoints/1/summary"),
                         ("post", "/endpoints/1/check"), ("delete", "/endpoints/1"), ("post", "/reports/export")]:
        assert getattr(anon, method)(path).status_code == 401, (method, path)


def test_health_is_public_and_checks_the_database(anon):
    r = anon.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "ok"}


def test_login_logout_me(app, db, password_hash):
    from app.db.models import User
    with db.session() as s:
        s.add(User(username="bob", password_hash=password_hash, role="viewer")); s.commit()
    c = TestClient(app)
    assert c.get("/auth/me").status_code == 401
    assert c.post("/auth/login", json={"username": "bob", "password": "wrong"}).status_code == 401
    r = c.post("/auth/login", json={"username": "BOB", "password": "Passw0rd!x"})  # username is case-insensitive
    assert r.status_code == 200 and r.json() == {"id": r.json()["id"], "username": "bob", "role": "viewer"}
    assert "password" not in r.text
    assert c.get("/auth/me").json()["username"] == "bob"
    assert c.post("/auth/logout").status_code == 204
    assert c.get("/auth/me").status_code == 401


def test_unknown_user_and_wrong_password_look_identical(anon, admin):
    a = anon.post("/auth/login", json={"username": "nobody", "password": "x"})
    b = anon.post("/auth/login", json={"username": "admin", "password": "x"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_login_is_rate_limited_after_repeated_failures(app, admin):
    app.state.login_limiter = RateLimiter(limit=3, window_seconds=300)
    c = TestClient(app)
    codes = [c.post("/auth/login", json={"username": "admin", "password": "bad"}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    # even the right password is refused while locked out
    assert c.post("/auth/login", json={"username": "admin", "password": "Passw0rd!x"}).status_code == 429


def test_deactivated_user_cannot_log_in_or_keep_a_session(app, db, admin):
    from app.db.models import User
    with db.session() as s:
        s.query(User).filter_by(username="admin").update({"is_active": False}); s.commit()
    assert admin.get("/endpoints").status_code == 401
    assert TestClient(app).post("/auth/login", json={"username": "admin", "password": "Passw0rd!x"}).status_code == 401


def test_cross_origin_writes_are_refused(admin):
    r = admin.post("/endpoints", json={"name": "x", "url": "http://127.0.0.1/"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    ok = admin.post("/endpoints", json={"name": "x", "url": "http://127.0.0.1:9/", "interval_seconds": 10, "timeout_seconds": 2},
                    headers={"Origin": "http://testserver"})
    assert ok.status_code == 201


def test_viewer_can_read_but_not_change_anything(admin, viewer, mock):
    ep = new_endpoint(admin, mock.url("/ok"))
    assert viewer.get("/endpoints").status_code == 200
    assert viewer.get(f"/endpoints/{ep['id']}").status_code == 200
    assert viewer.get(f"/endpoints/{ep['id']}/checks").status_code == 200
    assert viewer.get(f"/endpoints/{ep['id']}/summary").status_code == 200
    for method, path, body in [("post", "/endpoints", {"name": "n", "url": "http://x.example"}),
                               ("patch", f"/endpoints/{ep['id']}", {"enabled": False}),
                               ("delete", f"/endpoints/{ep['id']}", None),
                               ("post", f"/endpoints/{ep['id']}/check", None),
                               ("post", "/reports/export", {})]:
        r = getattr(viewer, method)(path, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path, r.text)
    assert admin.get(f"/endpoints/{ep['id']}").json()["enabled"] is True  # untouched


# ---- registering and managing endpoints ---------------------------------------------------

def test_register_and_read_back(admin, mock):
    r = admin.post("/endpoints", json={"name": "Example API", "url": mock.url("/ok"), "method": "GET",
                                       "expected_status": 200, "timeout_seconds": 5,
                                       "interval_seconds": 60, "failure_threshold": 3})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Example API" and body["enabled"] is True and body["latency_threshold_ms"] is None
    assert body["health"]["status"] == "unknown" and body["health"]["last_checked_at"] is None
    assert admin.get(f"/endpoints/{body['id']}").json() == body


def test_defaults_come_from_settings(admin, settings, mock):
    body = admin.post("/endpoints", json={"name": "d", "url": mock.url("/ok")}).json()
    assert body["interval_seconds"] == settings.default_interval_seconds
    assert body["timeout_seconds"] == settings.default_timeout_seconds
    assert body["failure_threshold"] == settings.default_failure_threshold


@pytest.mark.parametrize("override, field", [
    ({"method": "DELETE"}, "method"), ({"method": "POST"}, "method"),
    ({"expected_status": 99}, "expected_status"), ({"expected_status": 600}, "expected_status"),
    ({"timeout_seconds": 0}, "timeout_seconds"), ({"interval_seconds": 0}, "interval_seconds"),
    ({"failure_threshold": 0}, "failure_threshold"), ({"name": ""}, "name"), ({"name": "x" * 101}, "name"),
    ({"url": "x" * 2100}, "url"), ({"latency_threshold_ms": 0}, "latency_threshold_ms"),
])
def test_invalid_input_is_rejected(admin, mock, override, field):
    body = {"name": "n", "url": mock.url("/ok"), "interval_seconds": 10, "timeout_seconds": 2, **override}
    r = admin.post("/endpoints", json=body)
    assert r.status_code == 422
    assert field in str(r.json()["detail"])


def test_unknown_fields_are_rejected(admin, mock):
    r = admin.post("/endpoints", json={"name": "n", "url": mock.url("/ok"), "hack": 1})
    assert r.status_code == 422


@pytest.mark.parametrize("url", ["ftp://example.com", "file:///etc/passwd", "javascript:alert(1)",
                                 "http://user:pw@example.com/", "not a url", "example.com"])
def test_bad_urls_are_rejected(admin, url):
    r = admin.post("/endpoints", json={"name": "n", "url": url})
    assert r.status_code == 422, url


def test_interval_and_timeout_limits_from_settings(admin, mock):
    r = admin.post("/endpoints", json={"name": "n", "url": mock.url("/ok"), "interval_seconds": 60, "timeout_seconds": 31})
    assert r.status_code == 422 and "at most" in r.json()["detail"]
    r = admin.post("/endpoints", json={"name": "n", "url": mock.url("/ok"), "interval_seconds": 5, "timeout_seconds": 5})
    assert r.status_code == 422 and "shorter" in r.json()["detail"]


def test_ssrf_targets_are_rejected_when_private_targets_are_not_allowed(tmp_path, password_hash):
    from app.db.models import User
    s = Settings(_env_file=None, secret_key="k", database_url=f"sqlite:///{tmp_path / 'ssrf.db'}",
                 allow_private_targets=False, min_interval_seconds=1)
    application = create_app(s)
    application.state.db.create_schema()
    with application.state.db.session() as sess:
        sess.add(User(username="a", password_hash=password_hash, role="admin")); sess.commit()
    c = TestClient(application)
    assert c.post("/auth/login", json={"username": "a", "password": "Passw0rd!x"}).status_code == 200
    for url in ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.1:8080/", "http://192.168.1.1/", "http://[::1]/", "http://0.0.0.0/",
                "http://[::ffff:169.254.169.254]/"]:
        r = c.post("/endpoints", json={"name": "ssrf", "url": url, "interval_seconds": 60})
        assert r.status_code == 422, url
        assert "non-public" in r.json()["detail"] or "private" in r.json()["detail"], r.text
    # editing an existing endpoint cannot be used to smuggle in an internal URL either
    ok = c.post("/endpoints", json={"name": "ok", "url": "http://8.8.8.8/", "interval_seconds": 60})
    assert ok.status_code == 201
    r = c.patch(f"/endpoints/{ok.json()['id']}", json={"url": "http://169.254.169.254/"})
    assert r.status_code == 422
    assert c.get(f"/endpoints/{ok.json()['id']}").json()["url"] == "http://8.8.8.8/"
    application.state.db.dispose()


def test_list_filter_and_pagination(admin, mock):
    for i in range(5):
        new_endpoint(admin, mock.url("/ok"), name=f"api {i}", enabled=(i % 2 == 0))
    everything = admin.get("/endpoints").json()
    assert everything["total"] == 5 and len(everything["items"]) == 5
    assert [e["name"] for e in everything["items"]] == sorted(e["name"] for e in everything["items"])
    assert admin.get("/endpoints?enabled=false").json()["total"] == 2
    page = admin.get("/endpoints?limit=2&offset=2").json()
    assert len(page["items"]) == 2 and page["total"] == 5 and page["offset"] == 2
    assert admin.get("/endpoints?limit=0").status_code == 422


def test_update_only_changes_the_fields_sent(admin, mock):
    ep = new_endpoint(admin, mock.url("/ok"), latency_threshold_ms=500)
    r = admin.patch(f"/endpoints/{ep['id']}", json={"name": "renamed", "failure_threshold": 5})
    body = r.json()
    assert r.status_code == 200 and body["name"] == "renamed" and body["failure_threshold"] == 5
    assert body["url"] == ep["url"] and body["interval_seconds"] == ep["interval_seconds"]
    assert body["latency_threshold_ms"] == 500
    assert admin.patch(f"/endpoints/{ep['id']}", json={"clear_latency_threshold": True}).json()["latency_threshold_ms"] is None


def test_update_validation(admin, mock):
    ep = new_endpoint(admin, mock.url("/ok"))
    assert admin.patch(f"/endpoints/{ep['id']}", json={"timeout_seconds": 20}).status_code == 422  # >= interval (10)
    assert admin.patch(f"/endpoints/{ep['id']}", json={"method": "PUT"}).status_code == 422
    assert admin.patch(f"/endpoints/{ep['id']}", json={"url": "ftp://x"}).status_code == 422
    assert admin.get(f"/endpoints/{ep['id']}").json()["timeout_seconds"] == 2  # failed update left no trace


def test_disable_and_enable(admin, mock):
    ep = new_endpoint(admin, mock.url("/ok"))
    off = admin.patch(f"/endpoints/{ep['id']}", json={"enabled": False}).json()
    assert off["enabled"] is False and off["health"]["status"] == "disabled"
    on = admin.patch(f"/endpoints/{ep['id']}", json={"enabled": True}).json()
    assert on["enabled"] is True and on["health"]["status"] == "unknown"


def test_shortening_the_interval_takes_effect_immediately(admin, worker, mock):
    from datetime import timedelta

    from app.timeutil import utcnow
    ep = new_endpoint(admin, mock.url("/ok"), interval_seconds=3600)
    worker.run_once()                                     # next check is now an hour away
    long_wait = admin.get(f"/endpoints/{ep['id']}").json()["next_check_at"]
    short = admin.patch(f"/endpoints/{ep['id']}", json={"interval_seconds": 10}).json()
    assert short["interval_seconds"] == 10 and short["next_check_at"] < long_wait
    from datetime import datetime
    due = datetime.fromisoformat(short["next_check_at"]).replace(tzinfo=None)
    assert due <= utcnow() + timedelta(seconds=11)


def test_delete_removes_the_endpoint_and_its_history(admin, worker, db, mock):
    from app.db.models import AlertState, CheckResult
    ep = new_endpoint(admin, mock.url("/ok"))
    worker.run_once()
    assert admin.get(f"/endpoints/{ep['id']}/checks").json()["total"] == 1
    assert admin.delete(f"/endpoints/{ep['id']}").status_code == 204
    assert admin.get(f"/endpoints/{ep['id']}").status_code == 404
    with db.session() as s:
        assert s.query(CheckResult).count() == 0 and s.query(AlertState).count() == 0


def test_unknown_ids_give_404(admin):
    for call in (admin.get("/endpoints/999"), admin.patch("/endpoints/999", json={"name": "x"}),
                 admin.delete("/endpoints/999"), admin.get("/endpoints/999/checks"),
                 admin.get("/endpoints/999/summary"), admin.post("/endpoints/999/check")):
        assert call.status_code == 404
        assert call.json() == {"detail": "Endpoint not found."}
