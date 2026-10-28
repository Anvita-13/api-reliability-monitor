from app.core.evaluator import evaluate


def test_expected_status_passes():
    v = evaluate(200, 200, 50)
    assert v.success and v.error_type is None


def test_unexpected_status_fails():
    v = evaluate(500, 200, 50)
    assert not v.success and v.error_type == "unexpected_status"
    assert "500" in v.error_message and "200" in v.error_message


def test_non_200_can_be_the_expected_status():
    assert evaluate(204, 204, 10).success
    assert evaluate(301, 301, 10).success


def test_slow_response_fails_only_above_the_threshold():
    assert evaluate(200, 200, 500, latency_threshold_ms=500).success  # equal is fine
    v = evaluate(200, 200, 501, latency_threshold_ms=500)
    assert not v.success and v.error_type == "slow_response"


def test_wrong_status_is_reported_before_slowness():
    assert evaluate(503, 200, 9000, latency_threshold_ms=100).error_type == "unexpected_status"


def test_no_threshold_means_any_speed_is_ok():
    assert evaluate(200, 200, 60_000, latency_threshold_ms=None).success
