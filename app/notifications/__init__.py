from app.config import Settings
from app.notifications.base import AlertMessage, Notifier
from app.notifications.console import ConsoleNotifier
from app.notifications.sns import SnsNotifier

__all__ = ["AlertMessage", "Notifier", "ConsoleNotifier", "SnsNotifier", "build_notifier"]


def build_notifier(settings: Settings) -> Notifier:
    if settings.notification_backend == "sns":
        return SnsNotifier(settings.sns_topic_arn, settings.aws_region)
    return ConsoleNotifier()
