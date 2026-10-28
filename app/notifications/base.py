from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit


def safe_url(url: str) -> str:
    """URL without query string, fragment or credentials: safe to put in logs and alerts."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc = f"{host}:{parts.port}" if parts.port else host
    return f"{parts.scheme}://{netloc}{parts.path}"


@dataclass(frozen=True)
class AlertMessage:
    kind: str  # "alert" | "recovery"
    endpoint_id: int
    endpoint_name: str
    url: str
    consecutive_failures: int
    detail: str
    occurred_at: datetime

    @property
    def subject(self) -> str:
        label = "DOWN" if self.kind == "alert" else "RECOVERED"
        return f"[API Monitor] {label}: {self.endpoint_name}"[:100]

    @property
    def body(self) -> str:
        when = self.occurred_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if self.kind == "alert":
            return (
                f"{self.endpoint_name} is unhealthy.\n"
                f"URL: {safe_url(self.url)}\n"
                f"Consecutive failures: {self.consecutive_failures}\n"
                f"Latest error: {self.detail}\n"
                f"Time: {when}\n"
            )
        return (
            f"{self.endpoint_name} has recovered and is healthy again.\n"
            f"URL: {safe_url(self.url)}\n"
            f"Time: {when}\n"
        )


class Notifier(Protocol):
    def send(self, message: AlertMessage) -> None: ...
