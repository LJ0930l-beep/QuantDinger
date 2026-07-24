"""Reducer-only, fail-closed facts for recovering an unknown submission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping
from uuid import UUID

from app.domain.order_contracts import Actor, EconomicOrderState, SubmissionAttemptState
from app.domain.order_state_machine import (
    AggregateType, AuthorizedTransition, EconomicOrderScope, SubmissionAttemptScope,
    TransitionCause, authorize_attempt_transition, authorize_order_transition, strict_utc,
)
from app.domain.venue_order_contracts import NormalizedOrderQuery, OrderQueryReference, OrderQueryStatus


OBSERVATION_CONTRACT_VERSION = "submission-recovery-observation-v1"
_RECOVERY_REDUCER_PROOF = object()


class SubmissionRecoveryContractError(ValueError):
    pass


class RecoveryIdentityConflict(SubmissionRecoveryContractError):
    pass


def _uuid(value: object, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise SubmissionRecoveryContractError(f"{field} must be a UUID") from exc


def _text(value: object, field: str, case: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SubmissionRecoveryContractError(f"{field} is required")
    value = value.strip()
    return value.lower() if case == "lower" else value.upper() if case == "upper" else value


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SubmissionRecoveryContractError(f"{field} must be a non-negative integer")
    return value


def _json(value: Mapping[str, Any]) -> str:
    def safe(item: Any) -> Any:
        if isinstance(item, float):
            raise SubmissionRecoveryContractError("canonical observation cannot contain binary float")
        if isinstance(item, Mapping):
            return {str(key): safe(nested) for key, nested in item.items()}
        if isinstance(item, (list, tuple)):
            return [safe(nested) for nested in item]
        return item
    try:
        return json.dumps(safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise SubmissionRecoveryContractError("canonical observation is not JSON-safe") from exc


@dataclass(frozen=True, slots=True)
class EconomicOrderRecoveryFact:
    id: str
    scope: EconomicOrderScope
    state: EconomicOrderState
    version: int
    last_event_seq: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "economic_order.id"))
        if type(self.scope) is not EconomicOrderScope or not isinstance(self.state, EconomicOrderState):
            raise SubmissionRecoveryContractError("invalid economic order fact")
        object.__setattr__(self, "version", _integer(self.version, "economic_order.version"))
        object.__setattr__(self, "last_event_seq", _integer(self.last_event_seq, "economic_order.last_event_seq"))
        if self.version != self.last_event_seq:
            raise SubmissionRecoveryContractError("economic order version/sequence drift")


@dataclass(frozen=True, slots=True)
class SubmissionAttemptRecoveryFact:
    id: str
    scope: SubmissionAttemptScope
    state: SubmissionAttemptState
    version: int
    last_event_seq: int
    venue_capability_snapshot_id: str
    recovery_policy_snapshot_id: str
    canonical_client_order_id: str
    venue_client_order_id: str
    client_id_algorithm_version: str
    broker_prefix_normalization_version: str
    broker_prefix: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "attempt.id"))
        if type(self.scope) is not SubmissionAttemptScope or not isinstance(self.state, SubmissionAttemptState):
            raise SubmissionRecoveryContractError("invalid submission attempt fact")
        object.__setattr__(self, "version", _integer(self.version, "attempt.version"))
        object.__setattr__(self, "last_event_seq", _integer(self.last_event_seq, "attempt.last_event_seq"))
        if self.version != self.last_event_seq:
            raise SubmissionRecoveryContractError("attempt version/sequence drift")
        for name in ("venue_capability_snapshot_id", "recovery_policy_snapshot_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        for name in ("canonical_client_order_id", "venue_client_order_id", "client_id_algorithm_version",
                     "broker_prefix_normalization_version", "broker_prefix"):
            object.__setattr__(self, name, _text(getattr(self, name), name))


@dataclass(frozen=True, slots=True)
class ExchangeOrderRecoveryFact:
    exchange_order_pk: str
    attempt_id: str
    economic_order_id: str
    exchange: str
    market_type: str
    account_scope: str
    instrument_id: str
    exchange_order_id: str
    venue_client_order_id: str

    def __post_init__(self) -> None:
        for name in ("exchange_order_pk", "attempt_id", "economic_order_id"):
            object.__setattr__(self, name, _uuid(getattr(self, name), name))
        object.__setattr__(self, "exchange", _text(self.exchange, "exchange", "lower"))
        object.__setattr__(self, "market_type", _text(self.market_type, "market_type", "lower"))
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        object.__setattr__(self, "instrument_id", _text(self.instrument_id, "instrument_id", "upper"))
        object.__setattr__(self, "exchange_order_id", _text(self.exchange_order_id, "exchange_order_id"))
        object.__setattr__(self, "venue_client_order_id", _text(self.venue_client_order_id, "venue_client_order_id"))


@dataclass(frozen=True, slots=True)
class VenueCapabilitySnapshotFact:
    id: str
    exchange: str
    market_type: str
    capability_version: str
    profile_hash: str
    query_by_exchange_order_id: bool
    query_by_client_order_id: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "capability.id"))
        object.__setattr__(self, "exchange", _text(self.exchange, "capability.exchange", "lower"))
        object.__setattr__(self, "market_type", _text(self.market_type, "capability.market_type", "lower"))
        object.__setattr__(self, "capability_version", _text(self.capability_version, "capability_version"))
        object.__setattr__(self, "profile_hash", _text(self.profile_hash, "profile_hash"))
        if not isinstance(self.query_by_exchange_order_id, bool) or not isinstance(self.query_by_client_order_id, bool):
            raise SubmissionRecoveryContractError("query capabilities must be boolean")


@dataclass(frozen=True, slots=True)
class RecoveryPolicySnapshotFact:
    id: str
    capability_snapshot_id: str
    exchange: str
    market_type: str
    policy_version: str
    policy_hash: str
    capability_query_by_client_order_id: bool
    client_id_query_authoritative: bool
    order_history_authoritative: bool
    fill_history_authoritative: bool
    not_found_min_query_count: int
    not_found_grace_seconds: int
    not_found_action: str = "KEEP_UNKNOWN"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "recovery_policy.id"))
        object.__setattr__(self, "capability_snapshot_id", _uuid(self.capability_snapshot_id, "capability_snapshot_id"))
        object.__setattr__(self, "exchange", _text(self.exchange, "policy.exchange", "lower"))
        object.__setattr__(self, "market_type", _text(self.market_type, "policy.market_type", "lower"))
        object.__setattr__(self, "policy_version", _text(self.policy_version, "policy_version"))
        object.__setattr__(self, "policy_hash", _text(self.policy_hash, "policy_hash"))
        for name in ("capability_query_by_client_order_id", "client_id_query_authoritative",
                     "order_history_authoritative", "fill_history_authoritative"):
            if not isinstance(getattr(self, name), bool):
                raise SubmissionRecoveryContractError(f"{name} must be boolean")
        object.__setattr__(self, "not_found_min_query_count", _integer(self.not_found_min_query_count, "not_found_min_query_count"))
        object.__setattr__(self, "not_found_grace_seconds", _integer(self.not_found_grace_seconds, "not_found_grace_seconds"))
        if self.not_found_min_query_count < 1 or self.not_found_action != "KEEP_UNKNOWN":
            raise SubmissionRecoveryContractError("only explicit KEEP_UNKNOWN policy is authorized")


@dataclass(frozen=True, slots=True)
class RecoveryObservation:
    attempt_id: str
    query_invocation_id: str
    observed_at: datetime
    payload: Mapping[str, Any]
    observation_source: str = "REST"
    canonical_payload_json: str = field(init=False, repr=False)
    payload_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _uuid(self.attempt_id, "observation.attempt_id"))
        object.__setattr__(self, "query_invocation_id", _uuid(self.query_invocation_id, "query_invocation_id"))
        object.__setattr__(self, "observed_at", strict_utc(self.observed_at, "observed_at"))
        if self.observation_source != "REST" or not isinstance(self.payload, Mapping):
            raise SubmissionRecoveryContractError("invalid recovery observation")
        canonical = _json(self.payload)
        object.__setattr__(self, "canonical_payload_json", canonical)
        object.__setattr__(self, "payload_hash", hashlib.sha256(canonical.encode("utf-8")).hexdigest())


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    order: EconomicOrderRecoveryFact
    attempt: SubmissionAttemptRecoveryFact
    capability: VenueCapabilitySnapshotFact
    policy: RecoveryPolicySnapshotFact
    exchange_order: ExchangeOrderRecoveryFact | None
    observation: RecoveryObservation
    order_transition: AuthorizedTransition | None
    attempt_transition: AuthorizedTransition | None
    disposition: str
    reducer_proof: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.reducer_proof is not _RECOVERY_REDUCER_PROOF:
            raise SubmissionRecoveryContractError("RecoveryDecision must be created by the recovery reducer")
        if self.order.state is not EconomicOrderState.SUBMISSION_UNKNOWN or self.attempt.state is not SubmissionAttemptState.UNKNOWN:
            raise SubmissionRecoveryContractError("recovery requires SUBMISSION_UNKNOWN and UNKNOWN facts")
        if self.attempt.scope.economic_order_id != self.order.id or self.attempt.scope.canonical_material() != {**self.order.scope.canonical_material(), "economic_order_id": self.order.id, "exchange": self.attempt.scope.exchange}:
            raise SubmissionRecoveryContractError("attempt/order scope mismatch")
        if self.capability.id != self.attempt.venue_capability_snapshot_id or self.policy.id != self.attempt.recovery_policy_snapshot_id:
            raise SubmissionRecoveryContractError("snapshot id mismatch")
        if self.policy.capability_snapshot_id != self.capability.id or self.capability.exchange != self.attempt.scope.exchange or self.capability.market_type != self.attempt.scope.market_type:
            raise SubmissionRecoveryContractError("capability scope mismatch")
        if self.policy.exchange != self.capability.exchange or self.policy.market_type != self.capability.market_type:
            raise SubmissionRecoveryContractError("policy scope mismatch")
        if self.policy.capability_query_by_client_order_id != self.capability.query_by_client_order_id:
            raise SubmissionRecoveryContractError("policy/capability query fact mismatch")
        if self.observation.attempt_id != self.attempt.id:
            raise SubmissionRecoveryContractError("observation attempt mismatch")
        for transition, aggregate, aggregate_type in ((self.order_transition, self.order, AggregateType.ECONOMIC_ORDER),
                                                       (self.attempt_transition, self.attempt, AggregateType.SUBMISSION_ATTEMPT)):
            if transition is None:
                continue
            if transition.aggregate_id != aggregate.id or transition.aggregate_type is not aggregate_type:
                raise SubmissionRecoveryContractError("transition aggregate mismatch")
            if transition.current_state != aggregate.state.value or transition.expected_version != aggregate.version:
                raise SubmissionRecoveryContractError("transition fact version/state mismatch")
            if transition.event_seq != transition.resulting_version or transition.evidence_hash != self.observation.payload_hash:
                raise SubmissionRecoveryContractError("transition evidence/version mismatch")
            if transition.occurred_at != self.observation.observed_at or transition.correlation_id != self.observation.payload["correlation_id"]:
                raise SubmissionRecoveryContractError("transition observation mismatch")
            if transition.canonical_payload_json != self.observation.canonical_payload_json:
                raise SubmissionRecoveryContractError("transition canonical material mismatch")
        if self.disposition == "OBSERVATION_ONLY" and (self.order_transition or self.attempt_transition):
            raise SubmissionRecoveryContractError("observation-only decision cannot contain transitions")
        if self.disposition in {"STATE_UPDATED", "RECONCILIATION_REQUIRED"} and self.order_transition is None:
            raise SubmissionRecoveryContractError("stateful recovery requires order transition")
        if self.disposition == "ATTEMPT_ACKED_ONLY" and (self.order_transition is not None or self.attempt_transition is None):
            raise SubmissionRecoveryContractError("attempt-only recovery has invalid transitions")


def _scope_matches(attempt: SubmissionAttemptRecoveryFact, query: NormalizedOrderQuery) -> bool:
    scope = attempt.scope
    return query.venue == scope.exchange and query.market_type == scope.market_type and query.account_scope == scope.account_scope and query.instrument == scope.instrument_id


def _identity_matches(attempt: SubmissionAttemptRecoveryFact, exchange_order: ExchangeOrderRecoveryFact | None, query: NormalizedOrderQuery) -> bool:
    if query.reference is OrderQueryReference.CLIENT_ORDER_ID:
        expected = attempt.venue_client_order_id
        if query.client_order_id != expected:
            return False
        if query.status is OrderQueryStatus.FOUND and query.exchange_order_id:
            if exchange_order is None or query.exchange_order_id != exchange_order.exchange_order_id:
                return False
        return not (query.status is OrderQueryStatus.FOUND and query.client_order_id and query.client_order_id != expected)
    if query.reference is OrderQueryReference.EXCHANGE_ORDER_ID:
        if exchange_order is None or query.exchange_order_id != exchange_order.exchange_order_id:
            return False
        if exchange_order.attempt_id != attempt.id or exchange_order.economic_order_id != attempt.scope.economic_order_id:
            return False
        if (exchange_order.exchange, exchange_order.market_type, exchange_order.account_scope, exchange_order.instrument_id) != (attempt.scope.exchange, attempt.scope.market_type, attempt.scope.account_scope, attempt.scope.instrument_id):
            return False
        return not (query.status is OrderQueryStatus.FOUND and query.client_order_id and query.client_order_id != exchange_order.venue_client_order_id)
    return False


def _capability_allows(capability: VenueCapabilitySnapshotFact, query: NormalizedOrderQuery) -> bool:
    return capability.query_by_client_order_id if query.reference is OrderQueryReference.CLIENT_ORDER_ID else capability.query_by_exchange_order_id


def _material(*, query: NormalizedOrderQuery, capability: VenueCapabilitySnapshotFact, policy: RecoveryPolicySnapshotFact,
              invocation_id: str, queried_at: datetime, correlation_id: str) -> Mapping[str, Any]:
    return {"observation_contract_version": OBSERVATION_CONTRACT_VERSION, "contract_version": "state-event-v1", "query_invocation_id": invocation_id,
            "correlation_id": correlation_id, "queried_at": queried_at.isoformat(), "query": {"reference": query.reference.value,
            "venue": query.venue, "market_type": query.market_type, "account_scope": query.account_scope,
            "instrument": query.instrument, "exchange_order_id": query.exchange_order_id, "client_order_id": query.client_order_id,
            "status": query.status.value, "normalized_state": query.normalized_state, "raw_state": query.raw_state},
            "capability": {"id": capability.id, "version": capability.capability_version, "profile_hash": capability.profile_hash},
            "policy": {"id": policy.id, "version": policy.policy_version, "policy_hash": policy.policy_hash, "action": policy.not_found_action}}


def decide_submission_recovery(*, order: EconomicOrderRecoveryFact, attempt: SubmissionAttemptRecoveryFact,
                               capability: VenueCapabilitySnapshotFact, policy: RecoveryPolicySnapshotFact,
                               exchange_order: ExchangeOrderRecoveryFact | None, query: NormalizedOrderQuery,
                               queried_at: datetime, correlation_id: str, query_invocation_id: str) -> RecoveryDecision:
    """Produce the only valid RecoveryDecision; never emits a venue command."""
    queried_at = strict_utc(queried_at, "queried_at")
    invocation_id = _uuid(query_invocation_id, "query_invocation_id")
    correlation_id = _text(correlation_id, "correlation_id")
    # Validate ingress state before making even an observation.
    if order.state is not EconomicOrderState.SUBMISSION_UNKNOWN or attempt.state is not SubmissionAttemptState.UNKNOWN:
        raise SubmissionRecoveryContractError("recovery ingress state is not unknown")
    material = _material(query=query, capability=capability, policy=policy, invocation_id=invocation_id,
                         queried_at=queried_at, correlation_id=correlation_id)
    observation = RecoveryObservation(attempt.id, invocation_id, queried_at, material)
    payload, evidence = material, observation.payload_hash
    def order_event(target: EconomicOrderState, reason: str, cause: TransitionCause) -> AuthorizedTransition:
        return authorize_order_transition(aggregate_id=order.id, aggregate_scope=order.scope, current_state=order.state,
            target_state=target, expected_version=order.version, cause=cause, actor=Actor.ADMIN, reason_code=reason,
            correlation_id=correlation_id, occurred_at=queried_at, evidence_hash=evidence, canonical_payload=payload,
            idempotency_key=f"recovery:{invocation_id}:order")
    def attempt_event(target: SubmissionAttemptState) -> AuthorizedTransition:
        return authorize_attempt_transition(aggregate_id=attempt.id, aggregate_scope=attempt.scope, current_state=attempt.state,
            target_state=target, expected_version=attempt.version, cause=TransitionCause.VENUE_OBSERVATION, actor=Actor.ADMIN,
            reason_code="VENUE_QUERY_FOUND", correlation_id=correlation_id, occurred_at=queried_at, evidence_hash=evidence,
            canonical_payload=payload, idempotency_key=f"recovery:{invocation_id}:attempt")
    identity_ok = _scope_matches(attempt, query) and _identity_matches(attempt, exchange_order, query)
    if query.status is OrderQueryStatus.CONFLICT or not identity_ok or (query.status is OrderQueryStatus.FOUND and not _capability_allows(capability, query)):
        return RecoveryDecision(order, attempt, capability, policy, exchange_order, observation,
                                order_event(EconomicOrderState.RECONCILIATION_REQUIRED, "RECOVERY_IDENTITY_OR_CAPABILITY_CONFLICT", TransitionCause.RECONCILIATION_CONFLICT),
                                None, "RECONCILIATION_REQUIRED", _RECOVERY_REDUCER_PROOF)
    if query.status is not OrderQueryStatus.FOUND:
        return RecoveryDecision(order, attempt, capability, policy, exchange_order, observation, None, None, "OBSERVATION_ONLY", _RECOVERY_REDUCER_PROOF)
    targets = {"SUBMITTED": EconomicOrderState.SUBMITTED, "PARTIALLY_FILLED": EconomicOrderState.PARTIALLY_FILLED,
               "FILLED": EconomicOrderState.FILLED, "REJECTED": EconomicOrderState.REJECTED,
               "CANCEL_REQUESTED": EconomicOrderState.RECONCILIATION_REQUIRED, "CANCELLING": EconomicOrderState.RECONCILIATION_REQUIRED,
               "CANCELLED": EconomicOrderState.RECONCILIATION_REQUIRED, "RECONCILIATION_REQUIRED": EconomicOrderState.RECONCILIATION_REQUIRED}
    if query.normalized_state == "SUBMISSION_UNKNOWN":
        return RecoveryDecision(order, attempt, capability, policy, exchange_order, observation, None,
                                attempt_event(SubmissionAttemptState.ACKED), "ATTEMPT_ACKED_ONLY", _RECOVERY_REDUCER_PROOF)
    target = targets.get(query.normalized_state)
    if target is None:
        raise SubmissionRecoveryContractError("unsupported FOUND normalized state")
    conflict = target is EconomicOrderState.RECONCILIATION_REQUIRED
    return RecoveryDecision(order, attempt, capability, policy, exchange_order, observation,
        order_event(target, "VENUE_CANCEL_OR_RECONCILIATION" if conflict else "VENUE_QUERY_FOUND",
                    TransitionCause.RECONCILIATION_CONFLICT if conflict else TransitionCause.VENUE_OBSERVATION),
        attempt_event(SubmissionAttemptState.REJECTED if query.normalized_state == "REJECTED" else SubmissionAttemptState.ACKED),
        "RECONCILIATION_REQUIRED" if conflict else "STATE_UPDATED", _RECOVERY_REDUCER_PROOF)
