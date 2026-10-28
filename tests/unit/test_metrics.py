from unittest.mock import MagicMock

from app.config import Settings
from app.metrics import CloudWatchMetrics, NullMetrics, build_metrics


def test_checks_processed_publishes_one_count_metric():
    client = MagicMock()
    CloudWatchMetrics("Monitor", client=client).checks_processed(3)
    client.put_metric_data.assert_called_once_with(
        Namespace="Monitor",
        MetricData=[{"MetricName": "ChecksRecorded", "Value": 3, "Unit": "Count"}],
    )


def test_a_metrics_failure_never_raises():
    client = MagicMock()
    client.put_metric_data.side_effect = RuntimeError("CloudWatch unreachable")
    CloudWatchMetrics("Monitor", client=client).checks_processed(1)  # must not raise


def test_metrics_are_off_by_default():
    assert isinstance(build_metrics(Settings(secret_key="x" * 32)), NullMetrics)
