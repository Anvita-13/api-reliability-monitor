"""Operational metrics the worker reports about itself (not the monitored APIs' results).

``ChecksRecorded`` lets CloudWatch raise an alarm when the whole fleet stops recording checks,
even if the servers themselves look healthy.
"""
import logging

from app.config import Settings

logger = logging.getLogger(__name__)


class NullMetrics:
    def checks_processed(self, count: int) -> None:
        pass


class CloudWatchMetrics:
    def __init__(self, namespace: str, region: str | None = None, client=None):
        if client is None:
            import boto3  # lazy: local development does not need AWS

            client = boto3.client("cloudwatch", region_name=region)
        self.client, self.namespace = client, namespace

    def checks_processed(self, count: int) -> None:
        try:
            self.client.put_metric_data(
                Namespace=self.namespace,
                MetricData=[{"MetricName": "ChecksRecorded", "Value": count, "Unit": "Count"}],
            )
        except Exception:  # noqa: BLE001 - a metrics outage must never stop monitoring
            logger.warning("Could not publish the ChecksRecorded metric", exc_info=True)


def build_metrics(settings: Settings):
    if settings.metrics_backend == "cloudwatch":
        return CloudWatchMetrics(settings.metrics_namespace, settings.aws_region)
    return NullMetrics()
