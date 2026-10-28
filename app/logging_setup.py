import logging


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx/httpcore log the full URL of every request they send. Monitored URLs may carry
    # secrets in the query string (?api_key=...), so keep those libraries quiet: our own log
    # lines identify an endpoint by id and name instead.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
