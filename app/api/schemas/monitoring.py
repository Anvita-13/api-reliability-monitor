from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckOut(BaseModel):
    id: int
    endpoint_id: int
    checked_at: datetime
    status_code: int | None
    latency_ms: int | None
    success: bool
    error_type: str | None
    error_message: str | None


class CheckListOut(BaseModel):
    items: list[CheckOut]
    total: int
    limit: int
    offset: int


class ManualCheckOut(BaseModel):
    check: CheckOut
    status: str
    event: str | None  # "alert" / "recovery" if this check changed the endpoint's state


class TrendPointOut(BaseModel):
    start: datetime
    checks: int
    availability_pct: float | None
    avg_latency_ms: float | None


class SummaryOut(BaseModel):
    endpoint_id: int
    hours: int
    since: datetime
    until: datetime
    current_status: str
    total_checks: int
    successful_checks: int
    failed_checks: int
    availability_pct: float | None
    avg_latency_ms: float | None
    min_latency_ms: int | None
    max_latency_ms: int | None
    p95_latency_ms: int | None
    incidents: int  # number of alerts raised in the window
    truncated: bool
    trend: list[TrendPointOut]


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint_id: int | None = None
    hours: int = Field(24, ge=1, le=720)
    format: Literal["csv", "json"] = "csv"


class ExportOut(BaseModel):
    key: str
    rows: int
    size_bytes: int
    download_url: str
