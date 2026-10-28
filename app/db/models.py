"""Database models. All timestamps are stored as naive UTC."""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.timeutil import utcnow

# Millisecond precision on MySQL (plain DATETIME only keeps whole seconds).
UTCDateTime = DateTime().with_variant(mysql.DATETIME(fsp=3), "mysql")
# BIGINT on MySQL; plain INTEGER on SQLite (needed for SQLite auto-increment).
BigId = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class Endpoint(Base):
    """A monitored API endpoint and how to check it."""

    __tablename__ = "endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(2048))
    method: Mapped[str] = mapped_column(String(4), default="GET")
    expected_status: Mapped[int] = mapped_column(Integer, default=200)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=5)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    failure_threshold: Mapped[int] = mapped_column(Integer, default=3)
    latency_threshold_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # When the worker should next check this endpoint (the worker "claims" due rows).
    next_check_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    state: Mapped["AlertState"] = relationship(
        back_populates="endpoint", uselist=False, cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_endpoints_due", "enabled", "next_check_at"),
        CheckConstraint("method IN ('GET','HEAD')", name="ck_endpoints_method"),
        CheckConstraint("expected_status BETWEEN 100 AND 599", name="ck_endpoints_status"),
        CheckConstraint("timeout_seconds >= 1", name="ck_endpoints_timeout"),
        CheckConstraint("interval_seconds >= 1", name="ck_endpoints_interval"),
        CheckConstraint("failure_threshold >= 1", name="ck_endpoints_threshold"),
    )


class CheckResult(Base):
    """One execution of a health check."""

    __tablename__ = "check_results"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    checked_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean)
    error_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_results_endpoint_time", "endpoint_id", "checked_at"),
        Index("ix_results_time", "checked_at"),
    )


class AlertState(Base):
    """Current health of an endpoint, plus a copy of its latest check (cheap list views)."""

    __tablename__ = "alert_state"

    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="unknown")  # unknown|healthy|unhealthy
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    status_since: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_alert_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    endpoint: Mapped[Endpoint] = relationship(back_populates="state")

    __table_args__ = (
        CheckConstraint("status IN ('unknown','healthy','unhealthy')", name="ck_state_status"),
    )


class AlertEvent(Base):
    """An alert or recovery notification that was raised (the incident log)."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))  # alert | recovery
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(255), default="")
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        Index("ix_events_endpoint_time", "endpoint_id", "created_at"),
        Index("ix_events_time", "created_at"),
        CheckConstraint("kind IN ('alert','recovery')", name="ck_events_kind"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default="viewer")  # admin | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (CheckConstraint("role IN ('admin','viewer')", name="ck_users_role"),)
