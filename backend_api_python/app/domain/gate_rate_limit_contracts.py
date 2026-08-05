"""Deterministic Gate transport retry and circuit-breaker contracts.

This module is intentionally pure.  It classifies an already observed
transport result and derives a retry/circuit decision; it does not perform
HTTP calls, sleep, read credentials, or mutate shared runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GateRateLimitContractError(ValueError):
    """The retry or circuit-breaker inputs are incomplete or invalid."""


class GateTransportError(str, Enum):
    """Typed transport outcomes used by the retry boundary."""

    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    SERVER = "SERVER"
    AUTHENTICATION = "AUTHENTICATION"
    NOT_FOUND = "NOT_FOUND"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNKNOWN = "UNKNOWN"


class GateCircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True, slots=True)
class GateRateLimitPolicy:
    """Explicit retry and circuit thresholds.

    ``max_attempts`` includes the first request.  Delay calculation is
    deterministic and deliberately has no jitter or wall-clock dependency;
    a caller may schedule the returned delay in its own worker.
    """

    max_attempts: int = 3
    base_delay_seconds: int = 1
    max_delay_seconds: int = 30
    failure_threshold: int = 3
    cooldown_seconds: int = 30

    def __post_init__(self) -> None:
        for name in (
            "max_attempts",
            "base_delay_seconds",
            "max_delay_seconds",
            "failure_threshold",
            "cooldown_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise GateRateLimitContractError(f"{name} must be a positive integer")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise GateRateLimitContractError("max_delay_seconds cannot be below base_delay_seconds")


@dataclass(frozen=True, slots=True)
class GateTransportFailure:
    """A normalized provider result safe to pass to policy evaluation."""

    kind: GateTransportError
    status_code: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GateTransportError):
            raise GateRateLimitContractError("kind must be GateTransportError")
        if self.status_code is not None and (
            isinstance(self.status_code, bool)
            or not isinstance(self.status_code, int)
            or self.status_code < 100
            or self.status_code > 599
        ):
            raise GateRateLimitContractError("status_code must be a valid HTTP status code")

    @property
    def retryable(self) -> bool:
        return self.kind in {
            GateTransportError.RATE_LIMIT,
            GateTransportError.TIMEOUT,
            GateTransportError.SERVER,
        }

    @property
    def contributes_to_circuit(self) -> bool:
        return self.retryable


@dataclass(frozen=True, slots=True)
class GateCircuitSnapshot:
    """Immutable state supplied to and returned by the pure transition funcs."""

    state: GateCircuitState = GateCircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at_seconds: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, GateCircuitState):
            raise GateRateLimitContractError("state must be GateCircuitState")
        if (
            isinstance(self.consecutive_failures, bool)
            or not isinstance(self.consecutive_failures, int)
            or self.consecutive_failures < 0
        ):
            raise GateRateLimitContractError("consecutive_failures must be non-negative")
        if self.opened_at_seconds is not None and (
            isinstance(self.opened_at_seconds, bool)
            or not isinstance(self.opened_at_seconds, int)
            or self.opened_at_seconds < 0
        ):
            raise GateRateLimitContractError("opened_at_seconds must be a non-negative integer")
        if self.state is GateCircuitState.OPEN and self.opened_at_seconds is None:
            raise GateRateLimitContractError("OPEN circuit requires opened_at_seconds")
        if self.state is GateCircuitState.CLOSED and self.opened_at_seconds is not None:
            raise GateRateLimitContractError("CLOSED circuit cannot carry opened_at_seconds")


def classify_gate_transport(*, status_code: int | None = None, error: GateTransportError | None = None) -> GateTransportFailure:
    """Map an observed status/typed error without inventing NOT_FOUND.

    Status codes are authoritative when present.  Unknown outcomes remain
    ``UNKNOWN`` and therefore fail closed at the retry boundary.
    """

    if status_code is not None:
        if isinstance(status_code, bool) or not isinstance(status_code, int) or not 100 <= status_code <= 599:
            raise GateRateLimitContractError("status_code must be a valid HTTP status code")
        if status_code in (401, 403):
            return GateTransportFailure(GateTransportError.AUTHENTICATION, status_code)
        if status_code in (404, 410):
            return GateTransportFailure(GateTransportError.NOT_FOUND, status_code)
        if status_code in (418, 429):
            return GateTransportFailure(GateTransportError.RATE_LIMIT, status_code)
        if 500 <= status_code <= 599:
            return GateTransportFailure(GateTransportError.SERVER, status_code)
        if 400 <= status_code <= 499:
            return GateTransportFailure(GateTransportError.INVALID_RESPONSE, status_code)
    if error is None:
        return GateTransportFailure(GateTransportError.UNKNOWN, status_code)
    if not isinstance(error, GateTransportError):
        raise GateRateLimitContractError("error must be GateTransportError")
    return GateTransportFailure(error, status_code)


def retry_delay_seconds(attempt_number: int, policy: GateRateLimitPolicy) -> int:
    """Return deterministic exponential backoff for a 1-based attempt."""

    if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number < 1:
        raise GateRateLimitContractError("attempt_number must be a positive integer")
    if not isinstance(policy, GateRateLimitPolicy):
        raise GateRateLimitContractError("policy must be GateRateLimitPolicy")
    return min(policy.max_delay_seconds, policy.base_delay_seconds * (2 ** (attempt_number - 1)))


def should_retry(failure: GateTransportFailure, attempt_number: int, policy: GateRateLimitPolicy) -> bool:
    """Retry only typed transient failures and only within max attempts."""

    if not isinstance(failure, GateTransportFailure):
        raise GateRateLimitContractError("failure must be GateTransportFailure")
    if isinstance(attempt_number, bool) or not isinstance(attempt_number, int) or attempt_number < 1:
        raise GateRateLimitContractError("attempt_number must be a positive integer")
    if not isinstance(policy, GateRateLimitPolicy):
        raise GateRateLimitContractError("policy must be GateRateLimitPolicy")
    return failure.retryable and attempt_number < policy.max_attempts


def circuit_allows_request(snapshot: GateCircuitSnapshot, now_seconds: int, policy: GateRateLimitPolicy) -> bool:
    """Return whether a request may be attempted at the supplied timestamp."""

    if not isinstance(snapshot, GateCircuitSnapshot) or not isinstance(policy, GateRateLimitPolicy):
        raise GateRateLimitContractError("snapshot and policy must be typed")
    if isinstance(now_seconds, bool) or not isinstance(now_seconds, int) or now_seconds < 0:
        raise GateRateLimitContractError("now_seconds must be a non-negative integer")
    if snapshot.state is not GateCircuitState.OPEN:
        return True
    return now_seconds - int(snapshot.opened_at_seconds) >= policy.cooldown_seconds


def record_circuit_result(
    snapshot: GateCircuitSnapshot,
    *,
    now_seconds: int,
    policy: GateRateLimitPolicy,
    success: bool = False,
    failure: GateTransportFailure | None = None,
) -> GateCircuitSnapshot:
    """Advance circuit state after one typed result, without side effects."""

    if not isinstance(snapshot, GateCircuitSnapshot) or not isinstance(policy, GateRateLimitPolicy):
        raise GateRateLimitContractError("snapshot and policy must be typed")
    if isinstance(now_seconds, bool) or not isinstance(now_seconds, int) or now_seconds < 0:
        raise GateRateLimitContractError("now_seconds must be a non-negative integer")
    if success:
        return GateCircuitSnapshot()
    if not isinstance(failure, GateTransportFailure):
        raise GateRateLimitContractError("a failure is required when success is false")
    if not failure.contributes_to_circuit:
        return snapshot
    if snapshot.state is GateCircuitState.HALF_OPEN:
        return GateCircuitSnapshot(GateCircuitState.OPEN, policy.failure_threshold, now_seconds)
    failures = snapshot.consecutive_failures + 1
    if failures >= policy.failure_threshold:
        return GateCircuitSnapshot(GateCircuitState.OPEN, failures, now_seconds)
    return GateCircuitSnapshot(GateCircuitState.CLOSED, failures)


def enter_half_open(snapshot: GateCircuitSnapshot, *, now_seconds: int, policy: GateRateLimitPolicy) -> GateCircuitSnapshot:
    """Materialize the cooldown transition for a single probe request."""

    if snapshot.state is not GateCircuitState.OPEN or not circuit_allows_request(snapshot, now_seconds, policy):
        return snapshot
    return GateCircuitSnapshot(GateCircuitState.HALF_OPEN, snapshot.consecutive_failures)

