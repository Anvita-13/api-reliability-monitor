"""Security helpers, notifications, storage adapters and configuration."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.notifications import SnsNotifier, build_notifier
from app.notifications.base import AlertMessage, safe_url
from app.notifications.console import ConsoleNotifier
from app.security import RateLimiter, hash_password, verify_password
from app.storage import LocalStorage, S3Storage, build_storage

# ---- passwords & rate limiting -------------------------------------------------------

def test_password_hash_roundtrip(password_hash):
    assert verify_password("Passw0rd!x", password_hash)
    assert not verify_password("passw0rd!x", password_hash)
    assert "Passw0rd" not in password_hash


def test_hashes_are_salted():
    assert hash_password("same-pass1", iterations=1000) != hash_password("same-pass1", iterations=1000)


@pytest.mark.parametrize("bad", ["", "plain", "pbkdf2_sha256$x$y$z", "other$1$AA==$AA=="])
def test_malformed_stored_hashes_never_verify(bad):
    assert not verify_password("anything", bad)


def test_rate_limiter_window():
    now = [0.0]
    limiter = RateLimiter(limit=3, window_seconds=10, clock=lambda: now[0])
    assert [limiter.allow("k") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("other")  # keys are independent
    now[0] = 10.5
    assert limiter.allow("k")  # the window has slid past the old events


def test_rate_limiter_blocked_record_reset():
    limiter = RateLimiter(limit=2, window_seconds=60)
    limiter.record("k"); limiter.record("k")
    assert limiter.blocked("k")
    limiter.reset("k")
    assert not limiter.blocked("k")


# ---- notifications -------------------------------------------------------------------

def message(kind="alert"):
    return AlertMessage(kind, 7, "Payment API", "https://api.example.com/pay?token=SECRET123", 3,
                        "Expected HTTP 200, got 503", datetime(2026, 1, 1, 12, 0, 0))


def test_alert_text_never_leaks_query_strings_or_credentials():
    m = message()
    assert "SECRET123" not in m.body and "token" not in m.body
    assert safe_url("https://u:p@host.example.com:8443/a/b?x=1#f") == "https://host.example.com:8443/a/b"
    assert "DOWN" in m.subject and "Payment API" in m.subject
    assert "Expected HTTP 200, got 503" in m.body and "3" in m.body


def test_recovery_text():
    m = message("recovery")
    assert "RECOVERED" in m.subject and "recovered" in m.body


def test_console_notifier_records_messages():
    n = ConsoleNotifier()
    n.send(message())
    assert len(n.sent) == 1


def test_sns_notifier_publishes_to_the_topic():
    client = MagicMock()
    SnsNotifier("arn:aws:sns:ap-south-1:123456789012:alerts", client=client).send(message())
    kwargs = client.publish.call_args.kwargs
    assert kwargs["TopicArn"].endswith(":alerts") and "DOWN" in kwargs["Subject"] and "SECRET123" not in kwargs["Message"]


def test_notifier_selection():
    s = Settings(_env_file=None, secret_key="x", notification_backend="console")
    assert isinstance(build_notifier(s), ConsoleNotifier)


# ---- storage -------------------------------------------------------------------------

def test_local_storage_roundtrip_and_key_validation(tmp_path):
    storage = LocalStorage(str(tmp_path))
    storage.save("exports/a.csv", b"x,y\n1,2\n", "text/csv")
    assert (tmp_path / "exports" / "a.csv").read_bytes() == b"x,y\n1,2\n"
    assert storage.response("exports/a.csv").status_code == 200
    assert storage.response("exports/missing.csv").status_code == 404
    for bad in ("../secret.txt", "exports/../../x", "other/a.csv", "exports/a/b.csv", "/etc/passwd"):
        with pytest.raises(ValueError):
            storage.save(bad, b"x", "text/plain")


def test_s3_storage_uses_a_private_encrypted_bucket_and_signed_urls():
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://signed.example/url"
    storage = S3Storage("my-bucket", prefix="monitor", expiry_seconds=120, client=client)
    storage.save("exports/a.csv", b"data", "text/csv")
    put = client.put_object.call_args.kwargs
    assert put["Bucket"] == "my-bucket" and put["Key"] == "monitor/exports/a.csv"
    assert put["ServerSideEncryption"] == "AES256"
    response = storage.response("exports/a.csv", download_name="a.csv")
    assert response.status_code == 302 and response.headers["location"] == "https://signed.example/url"
    assert client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 120
    with pytest.raises(ValueError):
        storage.save("../x", b"d", "text/plain")


def test_storage_selection(tmp_path):
    s = Settings(_env_file=None, secret_key="x", storage_backend="local", exports_dir=str(tmp_path))
    assert isinstance(build_storage(s), LocalStorage)


# ---- configuration ---------------------------------------------------------------------

def test_production_requires_a_real_secret_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", secret_key=None)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", secret_key="change-me")
    assert Settings(_env_file=None, app_env="production", secret_key="a-long-random-value").is_production


def test_development_generates_a_secret_key():
    s = Settings(_env_file=None, app_env="development", secret_key=None)
    assert s.secret_key and len(s.secret_key) >= 32


def test_backend_options_are_validated():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, secret_key="x", notification_backend="sns")  # no topic
    with pytest.raises(ValidationError):
        Settings(_env_file=None, secret_key="x", storage_backend="s3")  # no bucket
    with pytest.raises(ValidationError):
        Settings(_env_file=None, secret_key="x", storage_backend="floppy")


def test_database_url_is_built_from_parts_with_special_characters_in_the_password():
    s = Settings(_env_file=None, secret_key="x", db_user="u", db_password="p@ss:w/rd#1", db_host="h", db_name="d")
    assert s.sqlalchemy_url.password == "p@ss:w/rd#1" and s.sqlalchemy_url.host == "h"


def test_http_library_loggers_are_silenced_so_secrets_in_urls_never_reach_logs():
    import logging

    from app.logging_setup import configure_logging
    configure_logging("INFO")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
