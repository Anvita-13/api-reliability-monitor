"""``python -m app.worker`` - run the monitoring worker as its own process."""
import signal
import threading

from app.config import get_settings
from app.db.session import Database
from app.logging_setup import configure_logging
from app.metrics import build_metrics
from app.notifications import build_notifier
from app.worker.runner import Worker


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    db = Database(settings)
    worker = Worker(db, build_notifier(settings), settings, metrics=build_metrics(settings))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())  # finish the current batch, then exit
    try:
        worker.run_forever(stop)
    finally:
        worker.close()
        db.dispose()


if __name__ == "__main__":
    main()
