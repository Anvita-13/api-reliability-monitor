"""Application settings, read from environment variables (and a local .env file).

Nothing sensitive lives in the source code. On AWS the same variables are supplied by the
instance environment, Systems Manager Parameter Store or Secrets Manager.
"""
import secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- application ----
    app_env: str = "development"
    app_name: str = "API Reliability Monitor"
    log_level: str = "INFO"
    # Must be identical on every server behind a load balancer (sessions are signed cookies).
    secret_key: str | None = None
    session_cookie_secure: bool = False  # true once served over HTTPS
    session_max_age_hours: int = 8

    # ---- database (MySQL locally, Amazon RDS on AWS) ----
    database_url: str | None = None  # a full SQLAlchemy URL overrides the DB_* values
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "api_monitor"
    db_user: str = "root"
    db_password: str = ""
    db_ssl_ca: str | None = None  # RDS CA bundle path, to encrypt the connection

    # ---- monitoring rules ----
    default_timeout_seconds: int = 5
    default_interval_seconds: int = 60
    default_failure_threshold: int = 3
    min_interval_seconds: int = 10
    max_timeout_seconds: int = 30
    # SSRF protection: when false (the default) targets resolving to private, loopback,
    # link-local or otherwise non-public addresses are refused. Enable ONLY for local
    # development against a mock server on localhost.
    allow_private_targets: bool = False
    retention_days: int = 30  # check results older than this are purged
    max_summary_rows: int = 100_000

    # ---- background worker ----
    worker_poll_seconds: float = 2.0
    worker_concurrency: int = 10
    worker_batch_size: int = 50
    run_worker_in_api: bool = False  # run the worker inside the API process (single-process dev)

    # ---- notifications ----
    notification_backend: str = "console"  # console | sns
    sns_topic_arn: str | None = None
    aws_region: str | None = None

    # ---- operational metrics ----
    metrics_backend: str = "none"  # none | cloudwatch
    metrics_namespace: str = "Monitor"

    # ---- file storage for exported reports ----
    storage_backend: str = "local"  # local | s3
    exports_dir: str = "exports"
    s3_bucket: str | None = None
    s3_prefix: str = "monitor"
    s3_url_expiry_seconds: int = 300

    @model_validator(mode="after")
    def _check(self):
        if not self.secret_key:
            if self.app_env == "production":
                raise ValueError("SECRET_KEY must be set when APP_ENV=production")
            self.secret_key = secrets.token_hex(32)  # dev only: new key every start
        if self.app_env == "production" and self.secret_key in ("change-me", "dev"):
            raise ValueError("Set a strong, unique SECRET_KEY in production")
        if self.notification_backend not in ("console", "sns"):
            raise ValueError("NOTIFICATION_BACKEND must be 'console' or 'sns'")
        if self.notification_backend == "sns" and not self.sns_topic_arn:
            raise ValueError("SNS_TOPIC_ARN is required when NOTIFICATION_BACKEND=sns")
        if self.metrics_backend not in ("none", "cloudwatch"):
            raise ValueError("METRICS_BACKEND must be 'none' or 'cloudwatch'")
        if self.storage_backend not in ("local", "s3"):
            raise ValueError("STORAGE_BACKEND must be 'local' or 's3'")
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        return self

    @property
    def sqlalchemy_url(self):
        if self.database_url:
            return self.database_url
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password or None,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
