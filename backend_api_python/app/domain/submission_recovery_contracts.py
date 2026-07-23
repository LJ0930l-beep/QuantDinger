"""Pure, fail-closed recovery decisions for a persisted unknown submission.

The reducer consumes already persisted order/attempt facts plus a PR-04 typed
read-only query result.  It does not perform venue I/O and deliberately cannot
produce a submit, cancel, or retry command.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping
from uuid import UUID

from app.domain.order_contracts import Actor, EconomicOrderState, SubmissionAttemptState
from app.domain.order_state_machine import (
    AuthorizedTransition,
    OperationalAuthorizationError,
    TransitionCause,
    authorize_attempt_transition,
    authorize_order_transition,
    strict_utc,
)
from app.domain.venue_order_contracts import NormalizedOrderQuery, OrderQueryStatus


class SubmissionRecoveryContractError(ValueError):
    """Raised when persisted recovery facts cannot be safely reconciled."""


def _uuid(value: object, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SubmissionRecoveryContractError(f"{field} must be a UUID") from exc


def _text(value: object, field: str, *, case: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubmissionRecoveryContractError(f"{field} is required")
    result = value.strip()
    if case == "lower":
        result = result.lower()
    elif case == "upper":
        result = result.upper()
    return result


def _nonnegative(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SubmissionRecoveryContractError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class EconomicOrderRecoveryFact:
    id: str
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    state: EconomicOrderState
    version: int
    last_event_seq: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "economic_order.id"))
        object.__setattr__(self, "tenant_id", _nonnegative(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "credential_id", _nonnegative(self.credential_id, "credential_id"))
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", case="upper"))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", case="lower"))
        if not isinstance(self.state, EconomicOrderState):
            raise SubmissionRecoveryContractError("economic_order.state is invalid")
        object.__setattr__(self, "version", _nonnegative(self.version, "economic_order.version"))
        object.__setattr__(self, "last_event_seq", _nonnegative(self.last_event_seq, "economic_order.last_event_seq"))
        if self.version != self.last_event_seq:
            raise SubmissionRecoveryContractError("economic_order version/sequence drift")


@dataclass(frozen=True, slots=True)
class SubmissionAttemptRecoveryFact:
    id: str
    economic_order_id: str
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    exchange: str
    market_type: str
    state: SubmissionAttemptState
    version: int
    last_event_seq: int
    venue_capability_snapshot_id: str
    recovery_policy_snapshot_id: str
    canonical_client_order_id: str
    client_id_algorithm_version: str
    broker_prefix_normalization_version: str
    broker_prefix: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "attempt.id"))
        object.__setattr__(self, "economic_order_id", _uuid(self.economic_order_id, "attempt.economic_order_id"))
        object.__setattr__(self, "tenant_id", _nonnegative(self.tenant_id, "tenant_id"))
        object.__setattr__(self, "credential_id", _nonnegative(self.credential_id, "credential_id"))
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", case="upper"))
        object.__setattr__(self, "exchange", _text(self.exchange, "exchange", case="lower"))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", case="lower"))
        if not isinstance(self.state, SubmissionAttemptState):
            raise SubmissionRecoveryContractError("attempt.state is invalid")
        object.__setattr__(self, "version", _nonnegative(self.version, "attempt.version"))
        object.__setattr__(self, "last_event_seq", _nonnegative(self.last_event_seq, "attempt.last_event_seq"))
        if self.version != self.last_event_seq:
            raise SubmissionRecoveryContractError("attempt version/sequence drift")
        object.__setattr__(self, "venue_capability_snapshot_id", _uuid(self.venue_capability_snapshot_id, "venue_capability_snapshot_id"))
        object.__setattr__(self, "recovery_policy_snapshot_id", _uuid(self.recovery_policy_snapshot_id, "recovery_policy_snapshot_id"))
        object.__setattr__(self, "canonical_client_order_id", _text(self.canonical_client_order_id, "canonical_client_order_id"))
        object.__setattr__(self, "client_id_algorithm_version", _text(self.client_id_algorithm_version, "client_id_algorithm_version"))
        object.__setattr__(self, "broker_prefix_normalization_version", _text(self.broker_prefix_normalization_version, "broker_prefix_normalization_version"))
        object.__setattr__(self, "broker_prefix", _text(self.broker_prefix, "broker_prefix"))


@dataclass(frozen=True, slots=True)
class RecoveryPolicySnapshotFact:
    id: str
    capability_snapshot_id: str
    exchange: str
    market_type: str
    policy_version: str
    not_found_action: str = "KEEP_UNKNOWN"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "recovery_policy.id"))
        object.__setattr__(self, "capability_snapshot_id", _uuid(self.capability_snapshot_id, "capability_snapshot_id"))
        object.__setattr__(self, "exchange", _text(self.exchange, "recovery_policy.exchange", case="lower"))
        object.__setattr__(self, "market_type", _text(self.market_type, "recovery_policy.market_type", case="lower"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "recovery_policy.policy_version"))
        if self.not_found_action != "KEEP_UNKNOWN":
            raise SubmissionRecoveryContractError("only KEEP_UNKNOWN is authorized in this PR")


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    """Redacted, canonical evidence that may be appended without a transition."""

    attempt_id: str
    observation_source: str
    payload_hash: str
    observed_at: datetime
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _uuid(self.attempt_id, "observation.attempt_id"))
        if self.observation_source != "REST":
            raise SubmissionRecoveryContractError("recovery observations must be REST facts")
        object.__setattr__(self, "payload_hash", _text(self.payload_hash, "observation.payload_hash"))
        object.__setattr__(self, "observed_at", strict_utc(self.observed_at, "observation.observed_at"))
        if not isinstance(self.payload, Mapping):
            raise SubmissionRecoveryContractError("observation.payload must be a mapping")

    @property
    def canonical_payload_json(self) -> str:
        try:
            return json.dumps(self.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except (TypeError, ValueError) as exc:
            raise SubmissionRecoveryContractError("observation.payload must be JSON-safe") from exc


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    order: EconomicOrderRecoveryFact
    attempt: SubmissionAttemptRecoveryFact
    policy: RecoveryPolicySnapshotFact
    observation: RecoveryObservation
    order_transition: AuthorizedTransition | None
    attempt_transition: AuthorizedTransition | None
    disposition: str

    def __post_init__(self) -> None:
        if self.attempt.economic_order_id != self.order.id:
            raise SubmissionRecoveryContractError("attempt/order identity mismatch")
        if self.attempt.tenant_id != self.order.tenant_id or self.attempt.credential_id != self.order.credential_id:
            raise SubmissionRecoveryContractError("attempt/order credential scope mismatch")
        if self.attempt.account_scope != self.order.account_scope or self.attempt.instrument_id != self.order.instrument_id:
            raise SubmissionRecoveryContractError("attempt/order account or instrument mismatch")
        if self.attempt.market_type != self.order.market_type:
            raise SubmissionRecoveryContractError("attempt/order market scope mismatch")
        if self.policy.id != self.attempt.recovery_policy_snapshot_id or self.policy.capability_snapshot_id != self.attempt.venue_capability_snapshot_id:
            raise SubmissionRecoveryContractError("attempt snapshot identity mismatch")
        if self.policy.exchange != self.attempt.exchange or self.policy.market_type != self.attempt.market_type:
            raise SubmissionRecoveryContractError("attempt snapshot scope mismatch")
        if self.observation.attempt_id != self.attempt.id:
            raise SubmissionRecoveryContractError("observation attempt mismatch")


def _evidence_hash(query: NormalizedOrderQuery) -> str:
    material = {
        "account_scope": query.account_scope,
        "client_order_id": query.client_order_id,
        "exchange_order_id": query.exchange_order_id,
        "instrument": query.instrument,
        "market_type": query.market_type,
        "normalized_state": query.normalized_state,
        "raw_state": query.raw_state,
        "status": query.status.value,
        "venue": query.venue,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _query_scope_matches(attempt: SubmissionAttemptRecoveryFact, query: NormalizedOrderQuery) -> bool:
    return (
        query.venue == attempt.exchange
        and query.market_type == attempt.market_type
        and query.account_scope == attempt.account_scope
        and query.instrument == attempt.instrument_id
    )


def _event_key(attempt_id: str, evidence_hash: str, role: str) -> str:
    return f"recovery:{attempt_id}:{role}:{evidence_hash[:40]}"


def _event_payload(query: NormalizedOrderQuery, policy: RecoveryPolicySnapshotFact) -> Mapping[str, Any]:
    return {
        "policy_version": policy.policy_version,
        "query": {
            "client_order_id": query.client_order_id,
            "exchange_order_id": query.exchange_order_id,
            "normalized_state": query.normalized_state,
            "raw_state": query.raw_state,
            "status": query.status.value,
        },
    }


def decide_submission_recovery(
    *,
    order: EconomicOrderRecoveryFact,
    attempt: SubmissionAttemptRecoveryFact,
    policy: RecoveryPolicySnapshotFact,
    query: NormalizedOrderQuery,
    queried_at: datetime,
    correlation_id: str,
) -> RecoveryDecision:
    """Reduce one typed venue response to durable observation/event intent.

    This keeps NOT_FOUND as evidence only.  In particular, it never emits a
    CONFIRMED_ABSENT transition or a resubmission instruction.
    """

    queried_at = strict_utc(queried_at, "queried_at")
    evidence_hash = _evidence_hash(query)
    observation = RecoveryObservation(
        attempt_id=attempt.id,
        observation_source="REST",
        payload_hash=evidence_hash,
        observed_at=queried_at,
        payload=_event_payload(query, policy),
    )
    scope_matches = _query_scope_matches(attempt, query)
    payload = _event_payload(query, policy)

    def order_event(target: EconomicOrderState, reason: str, cause: TransitionCause) -> AuthorizedTransition:
        return authorize_order_transition(
            aggregate_id=order.id,
            current_state=order.state,
            target_state=target,
            expected_version=order.version,
            cause=cause,
            actor=Actor.ADMIN,
            reason_code=reason,
            correlation_id=correlation_id,
            occurred_at=queried_at,
            evidence_hash=evidence_hash,
            canonical_payload=payload,
            idempotency_key=_event_key(attempt.id, evidence_hash, "order"),
        )

    def attempt_ack(target: SubmissionAttemptState = SubmissionAttemptState.ACKED) -> AuthorizedTransition:
        return authorize_attempt_transition(
            aggregate_id=attempt.id,
            current_state=attempt.state,
            target_state=target,
            expected_version=attempt.version,
            cause=TransitionCause.VENUE_OBSERVATION,
            actor=Actor.ADMIN,
            reason_code="VENUE_QUERY_FOUND",
            correlation_id=correlation_id,
            occurred_at=queried_at,
            evidence_hash=evidence_hash,
            canonical_payload=payload,
            idempotency_key=_event_key(attempt.id, evidence_hash, "attempt"),
        )

    if not scope_matches or query.status is OrderQueryStatus.CONFLICT:
        return RecoveryDecision(
            order=order, attempt=attempt, policy=policy, observation=observation,
            order_transition=order_event(EconomicOrderState.RECONCILIATION_REQUIRED, "VENUE_SCOPE_OR_CONFLICT", TransitionCause.RECONCILIATION_CONFLICT),
            attempt_transition=None, disposition="RECONCILIATION_REQUIRED",
        )

    if query.status is not OrderQueryStatus.FOUND:
        return RecoveryDecision(
            order=order, attempt=attempt, policy=policy, observation=observation,
            order_transition=None, attempt_transition=None, disposition="OBSERVATION_ONLY",
        )

    target = query.normalized_state
    order_targets = {
        "SUBMITTED": EconomicOrderState.SUBMITTED,
        "PARTIALLY_FILLED": EconomicOrderState.PARTIALLY_FILLED,
        "FILLED": EconomicOrderState.FILLED,
        "REJECTED": EconomicOrderState.REJECTED,
        "CANCEL_REQUESTED": EconomicOrderState.RECONCILIATION_REQUIRED,
        "CANCELLING": EconomicOrderState.RECONCILIATION_REQUIRED,
        "CANCELLED": EconomicOrderState.RECONCILIATION_REQUIRED,
        "RECONCILIATION_REQUIRED": EconomicOrderState.RECONCILIATION_REQUIRED,
    }
    if target == "SUBMISSION_UNKNOWN":
        return RecoveryDecision(
            order=order, attempt=attempt, policy=policy, observation=observation,
            order_transition=None,
            attempt_transition=attempt_ack(),
            disposition="ATTEMPT_ACKED_ONLY",
        )
    order_target = order_targets.get(target)
    if order_target is None:
        raise SubmissionRecoveryContractError("unknown FOUND normalized state")
    cause = TransitionCause.RECONCILIATION_CONFLICT if order_target is EconomicOrderState.RECONCILIATION_REQUIRED else TransitionCause.VENUE_OBSERVATION
    reason = "VENUE_CANCEL_OR_RECONCILIATION" if cause is TransitionCause.RECONCILIATION_CONFLICT else "VENUE_QUERY_FOUND"
    return RecoveryDecision(
        order=order, attempt=attempt, policy=policy, observation=observation,
        order_transition=order_event(order_target, reason, cause),
        attempt_transition=attempt_ack(SubmissionAttemptState.REJECTED if target == "REJECTED" else SubmissionAttemptState.ACKED),
        disposition="STATE_UPDATED",
    )
