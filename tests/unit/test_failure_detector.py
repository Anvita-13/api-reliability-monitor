from app.core.failure_detector import (
    ALERT,
    HEALTHY,
    RECOVERY,
    UNHEALTHY,
    UNKNOWN,
    StateView,
    display_status,
    next_state,
)


def run(results, threshold=3, start=StateView()):
    """Feed a list of booleans through the state machine; return [(status, failures, event)]."""
    state, out = start, []
    for ok in results:
        t = next_state(state, ok, threshold)
        out.append((t.status, t.consecutive_failures, t.event))
        state = StateView(t.status, t.consecutive_failures)
    return out


def test_first_success_is_healthy_without_a_recovery_event():
    assert run([True]) == [(HEALTHY, 0, None)]


def test_alert_fires_exactly_at_the_threshold():
    out = run([True, False, False, False])
    assert [e for _, _, e in out] == [None, None, None, ALERT]
    assert out[-1][:2] == (UNHEALTHY, 3)


def test_below_threshold_does_not_alert_and_a_success_resets_the_count():
    out = run([False, False, True, False, False])
    assert all(e is None for _, _, e in out)
    assert out[-1] == (HEALTHY, 2, None)


def test_one_alert_per_outage_no_matter_how_long_it_lasts():
    out = run([False] * 10)
    assert [e for _, _, e in out].count(ALERT) == 1
    assert out[-1] == (UNHEALTHY, 10, None)


def test_recovery_fires_once_on_the_first_success_after_an_outage():
    out = run([False] * 3 + [True, True])
    events = [e for _, _, e in out]
    assert events == [None, None, ALERT, RECOVERY, None]
    assert out[-1] == (HEALTHY, 0, None)


def test_a_second_outage_alerts_again():
    out = run([False] * 3 + [True] + [False] * 3)
    assert [e for _, _, e in out].count(ALERT) == 2


def test_threshold_of_one_alerts_on_the_first_failure():
    assert run([False], threshold=1) == [(UNHEALTHY, 1, ALERT)]


def test_first_check_failing_from_unknown_still_counts():
    out = run([False, False], threshold=2)
    assert out == [(UNKNOWN, 1, None), (UNHEALTHY, 2, ALERT)]


def test_lowering_the_threshold_alerts_on_the_next_failure():
    t = next_state(StateView(HEALTHY, 4), False, failure_threshold=3)
    assert t.event == ALERT


def test_display_status():
    assert display_status("healthy", 0) == "healthy"
    assert display_status("healthy", 2) == "degraded"
    assert display_status("unknown", 1) == "degraded"
    assert display_status("unhealthy", 5) == "unhealthy"
    assert display_status("unhealthy", 5, enabled=False) == "disabled"
