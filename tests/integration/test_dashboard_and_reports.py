import csv
import io
import json

from tests.conftest import new_endpoint

# ---- dashboard pages -------------------------------------------------------------------

def test_pages_require_login(anon):
    for path in ("/dashboard", "/dashboard/endpoints/1"):
        r = anon.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"
    assert anon.get("/", follow_redirects=False).headers["location"] == "/dashboard"
    assert anon.get("/login").status_code == 200


def test_login_page_redirects_when_already_signed_in(admin):
    assert admin.get("/login", follow_redirects=False).headers["location"] == "/dashboard"


def test_dashboard_shows_endpoint_status_and_incidents(admin, worker, mock):
    ok = new_endpoint(admin, mock.url("/ok"), name="Auth API")
    bad = new_endpoint(admin, mock.url("/status/503"), name="Payment API", failure_threshold=1)
    worker.run_once()
    html = admin.get("/dashboard").text
    assert "Auth API" in html and "Payment API" in html
    assert 'badge healthy">healthy' in html and 'badge unhealthy">unhealthy' in html
    assert "went <span" in html and "Expected HTTP 200, got 503" in html       # the incident log
    assert f"/dashboard/endpoints/{ok['id']}" in html and f"/dashboard/endpoints/{bad['id']}" in html


def test_dashboard_never_shows_query_strings(admin, mock):
    new_endpoint(admin, mock.url("/ok") + "?api_key=TOPSECRET")
    page = admin.get("/dashboard").text
    assert "TOPSECRET" not in page
    assert "TOPSECRET" not in admin.get("/dashboard/endpoints/1").text.split("Edit settings")[0]


def test_partial_refresh_returns_only_the_live_fragment(admin, mock):
    new_endpoint(admin, mock.url("/ok"), name="Auth API")
    frag = admin.get("/dashboard?partial=true").text
    assert "Auth API" in frag and "<html" not in frag and "<table" in frag


def test_admin_sees_management_controls_viewer_does_not(admin, viewer, mock):
    ep = new_endpoint(admin, mock.url("/ok"))
    assert "Add endpoint" in admin.get("/dashboard").text
    assert "Add endpoint" not in viewer.get("/dashboard").text
    page_admin = admin.get(f"/dashboard/endpoints/{ep['id']}").text
    page_viewer = viewer.get(f"/dashboard/endpoints/{ep['id']}").text
    assert 'data-action="check"' in page_admin and 'data-action="delete"' in page_admin
    assert 'data-action' not in page_viewer and "Edit settings" not in page_viewer


def test_endpoint_page_renders_summary_chart_and_checks(admin, worker, mock):
    ep = new_endpoint(admin, mock.url("/ok"), name="Auth API")
    worker.run_once()
    html = admin.get(f"/dashboard/endpoints/{ep['id']}?hours=6").text
    assert "Auth API" in html and "Availability (6h)" in html and "<svg" in html and "<rect" in html
    assert "100.0%" in html or "100%" in html
    assert admin.get(f"/dashboard/endpoints/{ep['id']}?partial=true").text.count("<html") == 0
    assert admin.get(f"/dashboard/endpoints/{ep['id']}?hours=0").status_code == 422
    assert admin.get("/dashboard/endpoints/9999").status_code == 404


def test_html_is_escaped(admin, mock):
    new_endpoint(admin, mock.url("/ok"), name="<script>alert(1)</script>")
    html = admin.get("/dashboard").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_static_assets_and_security_headers(anon):
    assert anon.get("/static/css/style.css").status_code == 200
    assert anon.get("/static/js/app.js").status_code == 200
    r = anon.get("/login")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


# ---- reports -----------------------------------------------------------------------------

def seed_history(admin, worker, mock):
    from datetime import timedelta

    from app.timeutil import utcnow
    ep = new_endpoint(admin, mock.url("/flaky"), name="Flaky API", failure_threshold=2)
    t0 = utcnow()
    for n, mode in enumerate(["up", "down", "down", "up"]):
        mock.mode = mode
        worker.run_once(now=t0 + timedelta(seconds=n * 11))
    return ep


def test_export_csv_and_download(admin, viewer, worker, mock, tmp_path):
    ep = seed_history(admin, worker, mock)
    r = admin.post("/reports/export", json={"endpoint_id": ep["id"], "hours": 24, "format": "csv"})
    assert r.status_code == 201
    out = r.json()
    assert out["rows"] == 4 and out["key"].startswith("exports/") and out["key"].endswith(".csv")
    assert (tmp_path / "exports" / out["key"]).exists()          # stored via the storage layer

    for client in (admin, viewer):                                # anyone signed in can download
        dl = client.get(out["download_url"])
        assert dl.status_code == 200 and dl.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(admin.get(out["download_url"]).text.lstrip("﻿"))))
    assert len(rows) == 4 and {r["endpoint_name"] for r in rows} == {"Flaky API"}
    assert [r["success"] for r in rows].count("false") == 2
    assert rows[0]["checked_at"].endswith("Z") and set(rows[0]) >= {"check_id", "status_code", "latency_ms", "error_type"}


def test_export_json_for_all_endpoints(admin, worker, mock):
    seed_history(admin, worker, mock)
    new_endpoint(admin, mock.url("/ok"), name="Other")
    worker.run_once()
    out = admin.post("/reports/export", json={"hours": 24, "format": "json"}).json()
    data = json.loads(admin.get(out["download_url"]).text)
    assert out["rows"] == len(data) == 5 and {d["endpoint_name"] for d in data} == {"Flaky API", "Other"}


def test_export_validation_and_permissions(admin, viewer, anon, mock):
    ep = new_endpoint(admin, mock.url("/ok"))
    assert admin.post("/reports/export", json={"hours": 0}).status_code == 422
    assert admin.post("/reports/export", json={"hours": 999}).status_code == 422
    assert admin.post("/reports/export", json={"format": "xml"}).status_code == 422
    assert admin.post("/reports/export", json={"endpoint_id": 999}).status_code == 404
    assert viewer.post("/reports/export", json={"endpoint_id": ep["id"]}).status_code == 403
    assert anon.post("/reports/export", json={}).status_code == 401
    assert anon.get("/reports/download/x.csv").status_code == 401


def test_download_rejects_bad_names_and_missing_files(admin):
    assert admin.get("/reports/download/does-not-exist.csv").status_code == 404
    for name in ("..%2F..%2Fapp%2Fconfig.py", "..%5Csecret", "a%2Fb.csv", ".env"):
        assert admin.get(f"/reports/download/{name}").status_code == 404, name


def test_csv_export_neutralises_spreadsheet_formulas(admin, db, mock):
    from app.db.models import CheckResult
    ep = new_endpoint(admin, mock.url("/ok"), name="=HYPERLINK(\"http://evil\")")
    with db.session() as s:
        s.add(CheckResult(endpoint_id=ep["id"], success=False, error_type="request_error",
                          error_message="+cmd|' /C calc'!A0"))
        s.commit()
    out = admin.post("/reports/export", json={"hours": 24, "format": "csv"}).json()
    text = admin.get(out["download_url"]).text
    assert "'=HYPERLINK" in text and "'+cmd" in text
