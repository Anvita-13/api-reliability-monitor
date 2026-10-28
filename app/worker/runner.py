"""Background worker: repeatedly claim due endpoints and check them.

Run on its own (``python -m app.worker``) or inside the API process
(``RUN_WORKER_IN_API=true``). Any number of workers on any number of servers can run at the
same time; the database claim (``FOR UPDATE SKIP LOCKED``) keeps them from overlapping.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta

import httpx

from app.config import Settings
from app.core.checker import build_client
from app.db.session import Database
from app.metrics import NullMetrics
from app.notifications.base import Notifier
from app.services import monitoring_service
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

PURGE_EVERY = timedelta(hours=1)


class Worker:
    def __init__(self, db: Database, notifier: Notifier, settings: Settings, client: httpx.Client | None = None,
                 metrics=None):
        self.db, self.notifier, self.settings = db, notifier, settings
        self.metrics = metrics or NullMetrics()
        self._owns_client = client is None
        self.client = client or build_client(settings.allow_private_targets)
        self.pool = ThreadPoolExecutor(max_workers=settings.worker_concurrency, thread_name_prefix="check")
        self._last_purge: datetime | None = None

    def _process(self, claimed) -> None:
        try:
            monitoring_service.process_claimed(self.db, self.notifier, self.client, claimed)
        except Exception:  # noqa: BLE001 - one bad endpoint must not stop the others
            logger.exception("Failed to process endpoint %s", claimed.endpoint_id)

    def run_once(self, now: datetime | None = None) -> int:
        """Claim everything that is due, check it concurrently, record the results."""
        claimed = monitoring_service.claim_due_endpoints(self.db, now or utcnow(), self.settings.worker_batch_size)
        if claimed:
            wait([self.pool.submit(self._process, c) for c in claimed])
            self.metrics.checks_processed(len(claimed))
        return len(claimed)

    def maybe_purge(self, now: datetime | None = None) -> None:
        now = now or utcnow()
        if self._last_purge is None or now - self._last_purge >= PURGE_EVERY:
            self._last_purge = now
            removed = monitoring_service.purge_old_results(self.db, now, self.settings.retention_days)
            if removed:
                logger.info("Purged %s check results older than %s days", removed, self.settings.retention_days)

    def run_forever(self, stop: threading.Event) -> None:
        logger.info("Worker started (poll every %ss, concurrency %s)", self.settings.worker_poll_seconds,
                    self.settings.worker_concurrency)
        while not stop.is_set():
            try:
                if self.run_once() == 0:
                    stop.wait(self.settings.worker_poll_seconds)
                self.maybe_purge()
            except Exception:  # noqa: BLE001 - e.g. the database is briefly unreachable
                logger.exception("Worker loop error; retrying shortly")
                stop.wait(self.settings.worker_poll_seconds * 2)
        logger.info("Worker stopped")

    def close(self) -> None:
        self.pool.shutdown(wait=True)
        if self._owns_client:
            self.client.close()
