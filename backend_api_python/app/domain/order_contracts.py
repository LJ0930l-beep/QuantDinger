"""Fail-closed contracts for the future unified order gateway.

This module is deliberately pure: it does not import repositories, workers,
exchange clients, or route code. PR-00 only defines vocabulary and validation;
it does not change any existing execution path.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import FrozenSet, Mapping, TypeVar


class OrderAction(str, Enum):
    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    CANCEL = "CANCEL"
    EMERGENCY_CLOSE = "EMERGENCY_CLOSE"
    PROTECTION = "PROTECTION"


class Actor(str, Enum):
    STRATEGY = "STRATEGY"
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    MCP = "MCP"
    GRID = "GRID"
    PROTECTION = "PROTECTION"
    ADMIN = "ADMIN"


class EconomicOrderState(str, Enum):
    CREATED = "CREATED"
    RISK_PENDING = "RISK_PENDING"
    RISK_RESERVED = "RISK_RESERVED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED = "SUBMITTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class SubmissionAttemptState(str, Enum):
    READY = "READY"
    SUBMITTING = "SUBMITTING"
    ACKED = "ACKED"
    UNKNOWN = "UNKNOWN"
    CONFIRMED_ABSENT = "CONFIRMED_ABSENT"
    REJECTED = "REJECTED"


class RiskEffect(str, Enum):
    INCREASE_RISK = "INCREASE_RISK"
    REDUCE_RISK = "REDUCE_RISK"
    NEUTRAL = "NEUTRAL"


class ReconciliationHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


class AmbiguousRiskEffectError(ValueError):
    """Raised when an action cannot safely be classified without context."""


_TRANSITIONS: Mapping[EconomicOrderState, FrozenSet[EconomicOrderState]] = MappingProxyType(
    {
        EconomicOrderState.CREATED: frozenset(
            {
                EconomicOrderState.RISK_PENDING,
                EconomicOrderState.REJECTED,
                EconomicOrderState.FAILED,
            }
        ),
        EconomicOrderState.RISK_PENDING: frozenset(
            {
                EconomicOrderState.RISK_RESERVED,
                EconomicOrderState.REJECTED,
                EconomicOrderState.FAILED,
                EconomicOrderState.RECONCILIATION_REQUIRED,
            }
        ),
        EconomicOrderState.RISK_RESERVED: frozenset(
            {
                EconomicOrderState.SUBMITTING,
                EconomicOrderState.CANCELLED,
                EconomicOrderState.FAILED,
                EconomicOrderState.RECONCILIATION_REQUIRED,
            }
        ),
        EconomicOrderState.SUBMITTING: frozenset(
            {
                EconomicOrderState.SUBMITTED,
                EconomicOrderState.SUBMISSION_UNKNOWN,
                EconomicOrderState.PARTIALLY_FILLED,
                EconomicOrderState.FILLED,
                EconomicOrderState.REJECTED,
                EconomicOrderState.FAILED,
            }
        ),
        EconomicOrderState.SUBMISSION_UNKNOWN: frozenset(
            {
                EconomicOrderState.SUBMITTING,
                EconomicOrderState.SUBMITTED,
                EconomicOrderState.PARTIALLY_FILLED,
                EconomicOrderState.FILLED,
                EconomicOrderState.REJECTED,
                EconomicOrderState.FAILED,
                EconomicOrderState.RECONCILIATION_REQUIRED,
            }
        ),
        EconomicOrderState.SUBMITTED: frozenset(
            {
                EconomicOrderState.PARTIALLY_FILLED,
                EconomicOrderState.FILLED,
                EconomicOrderState.CANCEL_REQUESTED,
                EconomicOrderState.CANCELLED,
                EconomicOrderState.REJECTED,
                EconomicOrderState.RECONCILIATION_REQUIRED,
            }
        ),
        EconomicOrderState.PARTIALLY_FILLED: frozenset(
            {
                EconomicOrderState.PARTIALLY_FILLED,
                EconomicOrderState.FILLED,
                EconomicOrderState.CANCEL_REQUESTED,
                EconomicOrderState.CANCELLED,
                EconomicOrderState.RECONCILIATION_REQUIRED,
            }
        ),
        EconomicOrderState.CANCEL_REQUESTED: frozenset(
            {
                EconomicOrderState.CANCELLING,
                EconomicOrderState.FILLED,
                EconomicOrderState.CANCELLED,
                EconomicOrderState.RECONCILIATION_REQUIRED,
            }
        ),
        EconomicOrderState.CANCELLING: frozenset(
            {
                EconomicOrderState.PARTIALLY_FILLED,
                EconomicOrderState.FILLED,
                EconomicOrderState.CANCELLED,
                EconomicOrderState.RECONCILIATION_REQUIRED,
            }
        ),
        EconomicOrderState.RECONCILIATION_REQUIRED: frozenset(
            {
                EconomicOrderState.SUBMITTED,
                EconomicOrderState.PARTIALLY_FILLED,
                EconomicOrderState.FILLED,
                EconomicOrderState.CANCELLED,
                EconomicOrderState.REJECTED,
                EconomicOrderState.FAILED,
            }
        ),
        EconomicOrderState.FILLED: frozenset(
            {EconomicOrderState.RECONCILIATION_REQUIRED}
        ),
        EconomicOrderState.CANCELLED: frozenset(
            {EconomicOrderState.RECONCILIATION_REQUIRED}
        ),
        EconomicOrderState.REJECTED: frozenset(
            {EconomicOrderState.RECONCILIATION_REQUIRED}
        ),
        EconomicOrderState.FAILED: frozenset(
            {EconomicOrderState.RECONCILIATION_REQUIRED}
        ),
    }
)

_BUSINESS_TERMINAL_STATES = frozenset(
    {
        EconomicOrderState.FILLED,
        EconomicOrderState.CANCELLED,
        EconomicOrderState.REJECTED,
        EconomicOrderState.FAILED,
    }
)

_ABSOLUTE_TERMINAL_STATES: FrozenSet[EconomicOrderState] = frozenset()

_RETRYABLE_STATES = frozenset(
    {
        EconomicOrderState.CREATED,
        EconomicOrderState.RISK_PENDING,
        EconomicOrderState.RISK_RESERVED,
        EconomicOrderState.SUBMISSION_UNKNOWN,
        EconomicOrderState.CANCEL_REQUESTED,
        EconomicOrderState.CANCELLING,
        EconomicOrderState.RECONCILIATION_REQUIRED,
    }
)

_QUERY_BEFORE_RETRY_STATES = frozenset(
    {
        EconomicOrderState.SUBMISSION_UNKNOWN,
        EconomicOrderState.CANCELLING,
        EconomicOrderState.RECONCILIATION_REQUIRED,
    }
)

_ATTEMPT_TRANSITIONS: Mapping[
    SubmissionAttemptState, FrozenSet[SubmissionAttemptState]
] = MappingProxyType(
    {
        SubmissionAttemptState.READY: frozenset(
            {SubmissionAttemptState.SUBMITTING}
        ),
        SubmissionAttemptState.SUBMITTING: frozenset(
            {
                SubmissionAttemptState.ACKED,
                SubmissionAttemptState.UNKNOWN,
                SubmissionAttemptState.REJECTED,
            }
        ),
        SubmissionAttemptState.UNKNOWN: frozenset(
            {
                SubmissionAttemptState.ACKED,
                SubmissionAttemptState.CONFIRMED_ABSENT,
                SubmissionAttemptState.REJECTED,
            }
        ),
        SubmissionAttemptState.ACKED: frozenset(),
        SubmissionAttemptState.CONFIRMED_ABSENT: frozenset(),
        SubmissionAttemptState.REJECTED: frozenset(),
    }
)

_E = TypeVar("_E", bound=Enum)


def _coerce_enum(value: object, enum_type: type[_E]) -> _E | None:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value.strip().upper())
        except ValueError:
            return None
    return None


def allowed_transitions(state: EconomicOrderState | str) -> FrozenSet[EconomicOrderState]:
    """Return the explicit transition set; unknown states have no exits."""

    current = _coerce_enum(state, EconomicOrderState)
    return _TRANSITIONS.get(current, frozenset()) if current is not None else frozenset()


def validate_transition(
    current: EconomicOrderState | str,
    target: EconomicOrderState | str,
) -> bool:
    """Return whether a transition is explicitly allowed.

    Unknown values, terminal exits, and omitted transitions fail closed.
    This function only validates the graph. Callers must separately enforce the
    exchange-query precondition for recovery states.
    """

    current_state = _coerce_enum(current, EconomicOrderState)
    target_state = _coerce_enum(target, EconomicOrderState)
    if current_state is None or target_state is None:
        return False
    return target_state in _TRANSITIONS[current_state]


def is_business_terminal_state(state: EconomicOrderState | str) -> bool:
    """Return whether normal business submission work is complete.

    Business-terminal states may still transition to
    ``RECONCILIATION_REQUIRED`` when late or conflicting external facts arrive.
    """

    parsed = _coerce_enum(state, EconomicOrderState)
    return parsed in _BUSINESS_TERMINAL_STATES if parsed is not None else False


def is_absolute_terminal_state(state: EconomicOrderState | str) -> bool:
    """Return whether no future fact can move the state.

    The approved order graph has no absolute terminal states because even a
    business-terminal order can receive contradictory exchange evidence.
    """

    parsed = _coerce_enum(state, EconomicOrderState)
    return parsed in _ABSOLUTE_TERMINAL_STATES if parsed is not None else False


def is_terminal_state(state: EconomicOrderState | str) -> bool:
    """Backward-compatible alias for ``is_business_terminal_state``."""

    return is_business_terminal_state(state)


def may_retry(state: EconomicOrderState | str) -> bool:
    """Return whether state-machine work may be retried, never blind submit."""

    parsed = _coerce_enum(state, EconomicOrderState)
    return parsed in _RETRYABLE_STATES if parsed is not None else False


def requires_exchange_query_before_retry(state: EconomicOrderState | str) -> bool:
    """Unknown input also returns True so a caller cannot infer safe retry."""

    parsed = _coerce_enum(state, EconomicOrderState)
    if parsed is None:
        return True
    return parsed in _QUERY_BEFORE_RETRY_STATES


def allowed_attempt_transitions(
    state: SubmissionAttemptState | str,
) -> FrozenSet[SubmissionAttemptState]:
    """Return the explicit attempt transition set; unknown states fail closed."""

    current = _coerce_enum(state, SubmissionAttemptState)
    if current is None:
        return frozenset()
    return _ATTEMPT_TRANSITIONS.get(current, frozenset())


def validate_attempt_transition(
    current: SubmissionAttemptState | str,
    target: SubmissionAttemptState | str,
) -> bool:
    """Return whether a submission-attempt transition is explicitly allowed."""

    current_state = _coerce_enum(current, SubmissionAttemptState)
    target_state = _coerce_enum(target, SubmissionAttemptState)
    if current_state is None or target_state is None:
        return False
    return target_state in _ATTEMPT_TRANSITIONS[current_state]


def attempt_requires_exchange_query(state: SubmissionAttemptState | str) -> bool:
    """Require exchange evidence for UNKNOWN and fail closed for unknown input."""

    parsed = _coerce_enum(state, SubmissionAttemptState)
    if parsed is None:
        return True
    return parsed is SubmissionAttemptState.UNKNOWN


def classify_risk_effect(
    action: OrderAction | str,
    *,
    protection_effect: RiskEffect | str | None = None,
) -> RiskEffect:
    parsed = _coerce_enum(action, OrderAction)
    if parsed is None:
        raise ValueError("unknown order action")
    if parsed in {OrderAction.OPEN, OrderAction.INCREASE}:
        return RiskEffect.INCREASE_RISK
    if parsed in {OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE}:
        return RiskEffect.REDUCE_RISK
    if parsed is OrderAction.CANCEL:
        return RiskEffect.NEUTRAL
    explicit_effect = _coerce_enum(protection_effect, RiskEffect)
    if explicit_effect is None:
        raise AmbiguousRiskEffectError("PROTECTION requires an explicit RiskEffect")
    return explicit_effect


def is_action_allowed(
    action: OrderAction | str,
    health: ReconciliationHealth | str,
    *,
    risk_effect: RiskEffect | str | None = None,
    actor: Actor | str | None = None,
) -> bool:
    """Apply reconciliation gating without actor-based overrides.

    ``actor`` is accepted so boundaries can validate it, but no Actor,
    including ADMIN, can override the hard reconciliation rule.
    """

    parsed_action = _coerce_enum(action, OrderAction)
    parsed_health = _coerce_enum(health, ReconciliationHealth)
    if parsed_action is None or parsed_health is None:
        return False
    if actor is not None and _coerce_enum(actor, Actor) is None:
        return False
    try:
        effect = classify_risk_effect(parsed_action, protection_effect=risk_effect)
    except (AmbiguousRiskEffectError, ValueError):
        return False
    if parsed_health is ReconciliationHealth.HEALTHY:
        return True
    return effect in {RiskEffect.REDUCE_RISK, RiskEffect.NEUTRAL}
