"""Availability and latency statistics from a list of check results. Pure logic, no I/O."""
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Row:
    """The minimum needed from a stored check."""
    checked_at: datetime
    success: bool
    status_code: int | None
    latency_ms: int | None


@dataclass
class TrendPoint:
    start: datetime
    checks: int = 0
    failures: int = 0
    latencies: list[int] = field(default_factory=list)

    @property
    def availability_pct(self) -> float | None:
        return availability_pct(self.checks - self.failures, self.checks)

    @property
    def avg_latency_ms(self) -> float | None:
        return round(sum(self.latencies) / len(self.latencies), 1) if self.latencies else None


def availability_pct(successes: int, total: int) -> float | None:
    return round(100.0 * successes / total, 2) if total else None


def nearest_rank_index(n: int, percentile: float) -> int:
    """0-based index of the nearest-rank percentile in a sorted list of n values."""
    return max(0, math.ceil(percentile / 100.0 * n) - 1)


def summarize(rows: list[Row], since: datetime, until: datetime, buckets: int = 24) -> dict:
    """Totals, latency stats and a bucketed trend for the window [since, until]."""
    total = len(rows)
    successes = sum(1 for r in rows if r.success)
    # Latency stats only use checks that actually got a response (a timeout's "latency" is
    # just the timeout value and would distort the numbers).
    latencies = sorted(r.latency_ms for r in rows if r.status_code is not None and r.latency_ms is not None)

    stats = {
        "total_checks": total,
        "successful_checks": successes,
        "failed_checks": total - successes,
        "availability_pct": availability_pct(successes, total),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "min_latency_ms": latencies[0] if latencies else None,
        "max_latency_ms": latencies[-1] if latencies else None,
        "p95_latency_ms": latencies[nearest_rank_index(len(latencies), 95)] if latencies else None,
    }

    window = max((until - since).total_seconds(), 1)
    bucket_seconds = max(60, math.ceil(window / buckets / 60) * 60)
    count = math.ceil(window / bucket_seconds)
    points = [TrendPoint(start=since + timedelta(seconds=i * bucket_seconds)) for i in range(count)]
    for r in rows:
        idx = int((r.checked_at - since).total_seconds() // bucket_seconds)
        if 0 <= idx < count:
            p = points[idx]
            p.checks += 1
            if not r.success:
                p.failures += 1
            if r.status_code is not None and r.latency_ms is not None:
                p.latencies.append(r.latency_ms)
    stats["trend"] = [
        {"start": p.start, "checks": p.checks, "availability_pct": p.availability_pct,
         "avg_latency_ms": p.avg_latency_ms}
        for p in points
    ]
    return stats
