from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EndpointCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)
    method: Literal["GET", "HEAD"] = "GET"
    expected_status: int = Field(200, ge=100, le=599)
    timeout_seconds: int | None = Field(None, ge=1, le=60)
    interval_seconds: int | None = Field(None, ge=1, le=86400)
    failure_threshold: int | None = Field(None, ge=1, le=100)
    latency_threshold_ms: int | None = Field(None, ge=1, le=600_000)
    enabled: bool = True


class EndpointUpdate(BaseModel):
    """PATCH body: only the fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(None, min_length=1, max_length=100)
    url: str | None = Field(None, min_length=1, max_length=2048)
    method: Literal["GET", "HEAD"] | None = None
    expected_status: int | None = Field(None, ge=100, le=599)
    timeout_seconds: int | None = Field(None, ge=1, le=60)
    interval_seconds: int | None = Field(None, ge=1, le=86400)
    failure_threshold: int | None = Field(None, ge=1, le=100)
    latency_threshold_ms: int | None = Field(None, ge=1, le=600_000)
    clear_latency_threshold: bool = False
    enabled: bool | None = None


class HealthOut(BaseModel):
    status: str  # healthy | degraded | unhealthy | unknown | disabled
    consecutive_failures: int
    status_since: datetime | None
    last_checked_at: datetime | None
    last_success: bool | None
    last_status_code: int | None
    last_latency_ms: int | None
    last_error_type: str | None


class EndpointOut(BaseModel):
    id: int
    name: str
    url: str
    method: str
    expected_status: int
    timeout_seconds: int
    interval_seconds: int
    failure_threshold: int
    latency_threshold_ms: int | None
    enabled: bool
    created_at: datetime
    next_check_at: datetime
    health: HealthOut


class EndpointListOut(BaseModel):
    items: list[EndpointOut]
    total: int
    limit: int
    offset: int
