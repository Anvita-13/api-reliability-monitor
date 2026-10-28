from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC timestamp, rounded down to milliseconds (the precision stored in MySQL, so a
    value read back from the database equals the one that was written)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    return now.replace(microsecond=now.microsecond // 1000 * 1000)


def as_utc(dt: datetime | None) -> datetime | None:
    """Mark a stored naive-UTC datetime as UTC so JSON output carries an explicit offset."""
    return dt.replace(tzinfo=UTC) if dt is not None else None


def iso_z(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="milliseconds") + "Z" if dt else None
