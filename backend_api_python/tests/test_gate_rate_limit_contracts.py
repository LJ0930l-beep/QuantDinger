"""Pure Gate retry and circuit-breaker contract tests."""

from app.domain.gate_rate_limit_contracts import (
    GateCircuitSnapshot,
    GateCircuitState,
    GateRateLimitContractError,
    GateRateLimitPolicy,
    GateTransportError,
    circuit_allows_request,
    classify_gate_transport,
    enter_half_open,
    record_circuit_result,
    retry_delay_seconds,
    should_retry,
)


def test_429_5xx_and_timeout_are_typed_transient_failures():
    assert classify_gate_transport(status_code=429).kind is GateTransportError.RATE_LIMIT
    assert classify_gate_transport(status_code=503).kind is GateTransportError.SERVER
    assert classify_gate_transport(error=GateTransportError.TIMEOUT).retryable is True


def test_auth_and_not_found_never_become_retryable():
    assert classify_gate_transport(status_code=401).retryable is False
    assert classify_gate_transport(status_code=403).kind is GateTransportError.AUTHENTICATION
    assert classify_gate_transport(status_code=404).kind is GateTransportError.NOT_FOUND
    assert classify_gate_transport(status_code=404).retryable is False


def test_unknown_transport_fails_closed():
    assert classify_gate_transport().kind is GateTransportError.UNKNOWN
    assert classify_gate_transport().retryable is False


def test_retry_budget_and_backoff_are_deterministic():
    policy = GateRateLimitPolicy(max_attempts=4, base_delay_seconds=2, max_delay_seconds=5)
    failure = classify_gate_transport(status_code=429)
    assert [retry_delay_seconds(n, policy) for n in (1, 2, 3, 4)] == [2, 4, 5, 5]
    assert should_retry(failure, 1, policy) is True
    assert should_retry(failure, 3, policy) is True
    assert should_retry(failure, 4, policy) is False


def test_circuit_opens_after_threshold_and_allows_one_half_open_probe():
    policy = GateRateLimitPolicy(failure_threshold=2, cooldown_seconds=10)
    failure = classify_gate_transport(status_code=502)
    first = record_circuit_result(GateCircuitSnapshot(), now_seconds=100, policy=policy, failure=failure)
    assert first.state is GateCircuitState.CLOSED
    second = record_circuit_result(first, now_seconds=101, policy=policy, failure=failure)
    assert second.state is GateCircuitState.OPEN
    assert circuit_allows_request(second, 105, policy) is False
    assert circuit_allows_request(second, 111, policy) is True
    half_open = enter_half_open(second, now_seconds=111, policy=policy)
    assert half_open.state is GateCircuitState.HALF_OPEN
    assert record_circuit_result(half_open, now_seconds=112, policy=policy, success=True) == GateCircuitSnapshot()


def test_non_transient_failure_does_not_open_circuit():
    policy = GateRateLimitPolicy(failure_threshold=1)
    auth = classify_gate_transport(status_code=403)
    snapshot = GateCircuitSnapshot()
    assert record_circuit_result(snapshot, now_seconds=10, policy=policy, failure=auth) == snapshot


def test_policy_and_inputs_fail_closed():
    for kwargs in (
        {"max_attempts": 0},
        {"base_delay_seconds": 0},
        {"max_delay_seconds": 1, "base_delay_seconds": 2},
    ):
        try:
            GateRateLimitPolicy(**kwargs)
        except GateRateLimitContractError:
            pass
        else:
            raise AssertionError("invalid policy accepted")
    try:
        retry_delay_seconds(0, GateRateLimitPolicy())
    except GateRateLimitContractError:
        pass
    else:
        raise AssertionError("invalid attempt accepted")

