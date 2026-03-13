from api.routers.admin.source_health import _classify_gate_status


def test_classify_gate_status_warming_when_samples_low():
    status = _classify_gate_status(
        samples=4,
        success_rate=0.0,
        timeout_rate=1.0,
        min_samples=10,
        min_success_rate=0.3,
        max_timeout_rate=0.45,
    )
    assert status == "warming"


def test_classify_gate_status_allowed_when_rates_within_thresholds():
    status = _classify_gate_status(
        samples=20,
        success_rate=0.7,
        timeout_rate=0.1,
        min_samples=10,
        min_success_rate=0.3,
        max_timeout_rate=0.45,
    )
    assert status == "allowed"


def test_classify_gate_status_blocked_when_rates_outside_thresholds():
    status = _classify_gate_status(
        samples=20,
        success_rate=0.1,
        timeout_rate=0.2,
        min_samples=10,
        min_success_rate=0.3,
        max_timeout_rate=0.45,
    )
    assert status == "blocked"
