import logging

from app.notifications.base import AlertMessage

logger = logging.getLogger("notifications")


class ConsoleNotifier:
    """Local development: write alerts to the log instead of sending them."""

    def __init__(self):
        self.sent: list[AlertMessage] = []  # handy for inspection in tests

    def send(self, message: AlertMessage) -> None:
        self.sent.append(message)
        level = logging.WARNING if message.kind == "alert" else logging.INFO
        logger.log(level, "%s\n%s", message.subject, message.body)
