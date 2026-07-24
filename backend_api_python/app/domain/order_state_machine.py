"""Pure, fail-closed authorization for durable order and attempt transitions.

This module deliberately reuses the structural state graphs from
``order_contracts``.  It adds the operational-authority layer required before
an event can be persisted; it never submits, cancels, or queries an exchange.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Mapping
from uuid import UUID

from app.domain.order_contracts import (
    Actor,
    EconomicOrderState,
    SubmissionAttemptState,
    validate_attempt_transition,
    validate_transition,
)


STATE_EVENT_CONTRACT_VERSION = "state-event-v1"
_REDUCER_PROOF = object()


class AggregateType(str, Enum):
    ECONOMIC_ORDER = "ECONOMIC_ORDER"
    SUBMISSION_ATTEMPT = "SUBMISSION_ATTEMPT"


class TransitionCause(str, Enum):
    RISK_DECISION = "RISK_DECISION"
    SUBMISSION_RESULT = "SUBMISSION_RESULT"
    VENUE_OBSERVATION = "VENUE_OBSERVATION"
    CANCEL_OBSERVATION = "CANCEL_OBSERVATION"
    RECONCILIATION_CONFLICT = "RECONCILIATION_CONFLICT"
    MANUAL_APPROVED_RECOVERY = "MANUAL_APPROVED_RECOVERY"


class StateMachineContractError(ValueError):
    """Base error for a transition that cannot be made durable."""


class UnknownStateError(StateMachineContractError):
    pass


class UnknownActorError(StateMachineContractError):
    pass


class UnknownTransitionCauseError(StateMachineContractError):
    pass


class StructuralTransitionError(StateMachineContractError):
    pass


class OperationalAuthorizationError(StateMachineContractError):
    pass


class StateEventVersionError(StateMachineContractError):
    pass


class StateEventConflict(StateMachineContractError):
    pass


@dataclass(frozen=True, slots=True)
class EconomicOrderScope:
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "credential_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StateMachineContractError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "account_scope", _required_text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _required_text(self.instrument_id, "instrument_id").upper())
        object.__setattr__(self, "market_type", _required_text(self.market_type, "market_type").lower())

    def canonical_material(self) -> Mapping[str, Any]:
        return {"tenant_id": self.tenant_id, "credential_id": self.credential_id,
                "account_scope": self.account_scope, "instrument_id": self.instrument_id,
                "market_type": self.market_type}


@dataclass(frozen=True, slots=True)
class SubmissionAttemptScope(EconomicOrderScope):
    economic_order_id: str
    exchange: str

    def __post_init__(self) -> None:
        EconomicOrderScope.__post_init__(self)
        object.__setattr__(self, "economic_order_id", _canonical_uuid(self.economic_order_id, "economic_order_id"))
        object.__setattr__(self, "exchange", _required_text(self.exchange, "exchange").lower())

    def canonical_material(self) -> Mapping[str, Any]:
        return {**EconomicOrderScope.canonical_material(self), "economic_order_id": self.economic_order_id, "exchange": self.exchange}


def _enum(value: object, enum_type: type[Enum], error_type: type[StateMachineContractError]) -> Enum:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value.strip().upper())
        except ValueError:
            pass
    raise error_type("unknown contract value")


def _canonical_uuid(value: object, field_name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise StateMachineContractError(f"{field_name} must be a UUID") from exc


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateMachineContractError(f"{field_name} is required")
    return value.strip()


def strict_utc(value: datetime, field_name: str = "occurred_at") -> datetime:
    """Accept only UTC/zero-offset datetimes and normalize to ``timezone.utc``."""

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise StateMachineContractError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise StateMachineContractError(f"{field_name} must be UTC or zero-offset")
    return value.astimezone(timezone.utc)


def _canonical_json(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise StateMachineContractError("canonical_payload must be a mapping")

    def reject_float(item: Any) -> Any:
        if isinstance(item, float):
            raise StateMachineContractError("canonical_payload cannot contain binary float")
        if isinstance(item, Mapping):
            return {str(key): reject_float(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [reject_float(nested) for nested in item]
        return item

    try:
        return json.dumps(reject_float(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise StateMachineContractError("canonical_payload is not JSON-safe") from exc


@dataclass(frozen=True, slots=True)
class AuthorizedTransition:
    """A fully authorized, immutable canonical state event.

    Repository APIs accept this value instead of free-form current/target
    strings.  The fingerprint intentionally omits any generated event UUID.
    """

    aggregate_id: str
    aggregate_type: AggregateType
    current_state: str
    target_state: str
    expected_version: int
    resulting_version: int
    event_seq: int
    transition_cause: TransitionCause
    actor: Actor
    reason_code: str
    correlation_id: str
    occurred_at: datetime
    evidence_hash: str
    canonical_payload: Mapping[str, Any]
    idempotency_key: str
    aggregate_scope: EconomicOrderScope | SubmissionAttemptScope
    reducer_proof: object = field(default=None, repr=False, compare=False)
    contract_version: str = STATE_EVENT_CONTRACT_VERSION
    canonical_payload_json: str = field(init=False, repr=False)
    event_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.reducer_proof is not _REDUCER_PROOF:
            raise StateMachineContractError("AuthorizedTransition must be created by an authorization reducer")
        object.__setattr__(self, "aggregate_id", _canonical_uuid(self.aggregate_id, "aggregate_id"))
        if not isinstance(self.aggregate_type, AggregateType):
            raise StateMachineContractError("aggregate_type is required")
        if self.aggregate_type is AggregateType.ECONOMIC_ORDER and type(self.aggregate_scope) is not EconomicOrderScope:
            raise StateMachineContractError("economic-order transition requires EconomicOrderScope")
        if self.aggregate_type is AggregateType.SUBMISSION_ATTEMPT and type(self.aggregate_scope) is not SubmissionAttemptScope:
            raise StateMachineContractError("attempt transition requires SubmissionAttemptScope")
        if not isinstance(self.expected_version, int) or isinstance(self.expected_version, bool) or self.expected_version < 0:
            raise StateEventVersionError("expected_version must be a non-negative integer")
        if self.resulting_version != self.expected_version + 1:
            raise StateEventVersionError("resulting_version must equal expected_version + 1")
        if self.event_seq != self.resulting_version:
            raise StateEventVersionError("event_seq must equal resulting_version")
        object.__setattr__(self, "reason_code", _required_text(self.reason_code, "reason_code"))
        object.__setattr__(self, "correlation_id", _required_text(self.correlation_id, "correlation_id"))
        object.__setattr__(self, "evidence_hash", _required_text(self.evidence_hash, "evidence_hash"))
        object.__setattr__(self, "idempotency_key", _required_text(self.idempotency_key, "idempotency_key"))
        if self.contract_version != STATE_EVENT_CONTRACT_VERSION:
            raise StateMachineContractError("unsupported state event contract_version")
        object.__setattr__(self, "occurred_at", strict_utc(self.occurred_at))
        payload_json = _canonical_json(self.canonical_payload)
        object.__setattr__(self, "canonical_payload_json", payload_json)
        material = {
            "aggregate_id": self.aggregate_id,
            "aggregate_type": self.aggregate_type.value,
            "aggregate_scope": self.aggregate_scope.canonical_material(),
            "canonical_payload": json.loads(payload_json),
            "contract_version": self.contract_version,
            "current_state": self.current_state,
            "event_seq": self.event_seq,
            "evidence_hash": self.evidence_hash,
            "expected_version": self.expected_version,
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.occurred_at.isoformat(),
            "reason_code": self.reason_code,
            "resulting_version": self.resulting_version,
            "target_state": self.target_state,
            "transition_cause": self.transition_cause.value,
            "actor": self.actor.value,
            "correlation_id": self.correlation_id,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "event_fingerprint", hashlib.sha256(encoded.encode("utf-8")).hexdigest())


def _order_cause_authorized(
    current: EconomicOrderState,
    target: EconomicOrderState,
    cause: TransitionCause,
) -> bool:
    if cause is TransitionCause.RECONCILIATION_CONFLICT:
        return target is EconomicOrderState.RECONCILIATION_REQUIRED
    if cause is TransitionCause.RISK_DECISION:
        return (current, target) in {
            (EconomicOrderState.CREATED, EconomicOrderState.RISK_PENDING),
            (EconomicOrderState.RISK_PENDING, EconomicOrderState.RISK_RESERVED),
            (EconomicOrderState.RISK_PENDING, EconomicOrderState.REJECTED),
            (EconomicOrderState.RISK_PENDING, EconomicOrderState.FAILED),
            (EconomicOrderState.RISK_PENDING, EconomicOrderState.RECONCILIATION_REQUIRED),
        }
    if cause is TransitionCause.SUBMISSION_RESULT:
        return current in {EconomicOrderState.RISK_RESERVED, EconomicOrderState.SUBMITTING}
    if cause is TransitionCause.VENUE_OBSERVATION:
        return current in {
            EconomicOrderState.SUBMISSION_UNKNOWN,
            EconomicOrderState.SUBMITTED,
            EconomicOrderState.PARTIALLY_FILLED,
        }
    if cause is TransitionCause.CANCEL_OBSERVATION:
        return current in {EconomicOrderState.CANCEL_REQUESTED, EconomicOrderState.CANCELLING}
    if cause is TransitionCause.MANUAL_APPROVED_RECOVERY:
        return current is EconomicOrderState.RECONCILIATION_REQUIRED and target is not EconomicOrderState.SUBMITTING
    return False


def _actor_cause_authorized(actor: Actor, cause: TransitionCause) -> bool:
    """The smallest explicit matrix; unspecified runtime principals fail closed."""
    if cause in {TransitionCause.VENUE_OBSERVATION, TransitionCause.CANCEL_OBSERVATION,
                 TransitionCause.RECONCILIATION_CONFLICT}:
        return actor is Actor.ADMIN
    if cause is TransitionCause.MANUAL_APPROVED_RECOVERY:
        return actor in {Actor.HUMAN, Actor.ADMIN}
    # PR-05 has no approved risk/execution runtime principal contract yet.
    return False


def _attempt_cause_authorized(
    current: SubmissionAttemptState,
    target: SubmissionAttemptState,
    cause: TransitionCause,
) -> bool:
    if (current, target) == (SubmissionAttemptState.UNKNOWN, SubmissionAttemptState.CONFIRMED_ABSENT):
        return False
    if current is SubmissionAttemptState.READY:
        return target is SubmissionAttemptState.SUBMITTING and cause is TransitionCause.SUBMISSION_RESULT
    if current is SubmissionAttemptState.SUBMITTING:
        return cause is TransitionCause.SUBMISSION_RESULT
    if current is SubmissionAttemptState.UNKNOWN:
        return cause in {TransitionCause.VENUE_OBSERVATION, TransitionCause.MANUAL_APPROVED_RECOVERY}
    return False


def _authorized_event(
    *,
    aggregate_id: object,
    aggregate_type: AggregateType,
    current_state: str,
    target_state: str,
    expected_version: int,
    cause: object,
    actor: object,
    reason_code: str,
    correlation_id: str,
    occurred_at: datetime,
    evidence_hash: str,
    canonical_payload: Mapping[str, Any],
    idempotency_key: str,
    aggregate_scope: EconomicOrderScope | SubmissionAttemptScope,
) -> AuthorizedTransition:
    parsed_cause = _enum(cause, TransitionCause, UnknownTransitionCauseError)
    parsed_actor = _enum(actor, Actor, UnknownActorError)
    return AuthorizedTransition(
        aggregate_id=str(aggregate_id),
        aggregate_type=aggregate_type,
        current_state=current_state,
        target_state=target_state,
        expected_version=expected_version,
        resulting_version=expected_version + 1,
        event_seq=expected_version + 1,
        transition_cause=parsed_cause,
        actor=parsed_actor,
        reason_code=reason_code,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        evidence_hash=evidence_hash,
        canonical_payload=canonical_payload,
        idempotency_key=idempotency_key,
        aggregate_scope=aggregate_scope,
        reducer_proof=_REDUCER_PROOF,
    )


def authorize_order_transition(
    *,
    aggregate_id: object,
    current_state: object,
    target_state: object,
    expected_version: int,
    cause: object,
    actor: object,
    reason_code: str,
    correlation_id: str,
    occurred_at: datetime,
    evidence_hash: str,
    canonical_payload: Mapping[str, Any],
    idempotency_key: str,
    aggregate_scope: EconomicOrderScope,
) -> AuthorizedTransition:
    """Authorize one EconomicOrder transition without persisting it."""

    current = _enum(current_state, EconomicOrderState, UnknownStateError)
    target = _enum(target_state, EconomicOrderState, UnknownStateError)
    parsed_cause = _enum(cause, TransitionCause, UnknownTransitionCauseError)
    if not validate_transition(current, target):
        raise StructuralTransitionError("economic order transition is not in the structural graph")
    if (current, target) == (EconomicOrderState.SUBMISSION_UNKNOWN, EconomicOrderState.SUBMITTING):
        raise OperationalAuthorizationError("unknown submission cannot resume submitting in this PR")
    if not _order_cause_authorized(current, target, parsed_cause):
        raise OperationalAuthorizationError("transition cause is not authorized for this economic order transition")
    if not _actor_cause_authorized(_enum(actor, Actor, UnknownActorError), parsed_cause):
        raise OperationalAuthorizationError("actor is not authorized for this transition cause")
    return _authorized_event(
        aggregate_id=aggregate_id,
        aggregate_type=AggregateType.ECONOMIC_ORDER,
        current_state=current.value,
        target_state=target.value,
        expected_version=expected_version,
        cause=parsed_cause,
        actor=actor,
        reason_code=reason_code,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        evidence_hash=evidence_hash,
        canonical_payload=canonical_payload,
        idempotency_key=idempotency_key,
        aggregate_scope=aggregate_scope,
    )


def authorize_attempt_transition(
    *,
    aggregate_id: object,
    current_state: object,
    target_state: object,
    expected_version: int,
    cause: object,
    actor: object,
    reason_code: str,
    correlation_id: str,
    occurred_at: datetime,
    evidence_hash: str,
    canonical_payload: Mapping[str, Any],
    idempotency_key: str,
    aggregate_scope: SubmissionAttemptScope,
) -> AuthorizedTransition:
    """Authorize one SubmissionAttempt transition without persisting it."""

    current = _enum(current_state, SubmissionAttemptState, UnknownStateError)
    target = _enum(target_state, SubmissionAttemptState, UnknownStateError)
    parsed_cause = _enum(cause, TransitionCause, UnknownTransitionCauseError)
    if not validate_attempt_transition(current, target):
        raise StructuralTransitionError("submission attempt transition is not in the structural graph")
    if (current, target) == (SubmissionAttemptState.UNKNOWN, SubmissionAttemptState.CONFIRMED_ABSENT):
        raise OperationalAuthorizationError("confirmed absent is intentionally unavailable in this PR")
    if not _attempt_cause_authorized(current, target, parsed_cause):
        raise OperationalAuthorizationError("transition cause is not authorized for this submission attempt transition")
    if not _actor_cause_authorized(_enum(actor, Actor, UnknownActorError), parsed_cause):
        raise OperationalAuthorizationError("actor is not authorized for this transition cause")
    return _authorized_event(
        aggregate_id=aggregate_id,
        aggregate_type=AggregateType.SUBMISSION_ATTEMPT,
        current_state=current.value,
        target_state=target.value,
        expected_version=expected_version,
        cause=parsed_cause,
        actor=actor,
        reason_code=reason_code,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        evidence_hash=evidence_hash,
        canonical_payload=canonical_payload,
        idempotency_key=idempotency_key,
        aggregate_scope=aggregate_scope,
    )
