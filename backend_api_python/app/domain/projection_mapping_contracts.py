"""Lossless, pure mapping of Admission Outbox V2 facts to projections.

The mapper is deliberately a boundary-only contract.  It parses the already
canonical Admission Outbox event and returns immutable, replayable facts for a
future Candidate Projection.  It performs no I/O and does not make a trading
decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json

from app.domain.entry_admission_v2_contracts import (
    AdmissionOutboxEventFactV2,
    EntryAdmissionError,
    parse_admission_outbox_event,
)
from app.domain.canonical_entry_contracts import EntryMode, EntrySource
from app.domain.outbox_projection_contracts import OutboxEvent, OutboxProjectionContractError
from app.domain.order_contracts import Actor, OrderAction, RiskEffect


PROJECTION_MAPPING_CONTRACT_VERSION = "candidate-projection-facts-v1"


class ProjectionMappingError(ValueError):
    """A canonical Admission event cannot be mapped losslessly."""


class ProjectionSubjectKind(str, Enum):
    ECONOMIC_ORDER = "ECONOMIC_ORDER"
    CANCEL_TARGET = "CANCEL_TARGET"


@dataclass(frozen=True, slots=True)
class CandidateProjectionFacts:
    """All immutable facts required to rebuild a Candidate Projection.

    ``canonical_payload`` is retained as immutable source evidence.  Typed
    fields provide safe projection access while the source evidence guarantees
    that mapping never silently drops a future/audit payload field.
    """

    mapping_contract_version: str
    event_id: str
    payload_hash: str
    canonical_payload: str
    aggregate_type: str
    aggregate_id: str
    aggregate_version: int
    event_type: str
    schema_version: str
    command_id: str
    action: OrderAction
    risk_effect: RiskEffect
    subject_kind: ProjectionSubjectKind
    subject_id: str
    economic_order_id: str | None
    economic_fingerprint: str
    request_fingerprint: str
    tenant_id: int
    credential_id: int
    account_scope: str
    instrument_id: str
    market_type: str
    actor_type: Actor
    actor_id: str
    source: EntrySource
    mode: EntryMode
    correlation_id: str
    occurred_at: datetime
    risk_decision_id: str | None
    risk_decision_status: str | None
    decision_fingerprint: str | None
    scope_fingerprint: str | None
    audit_fingerprint: str | None
    reservation_id: str | None

    @property
    def fingerprint(self) -> str:
        """Stable identity of the complete source and typed projection facts."""

        material = {
            "mapping_contract_version": self.mapping_contract_version,
            "event_id": self.event_id,
            "payload_hash": self.payload_hash,
            "canonical_payload": self.canonical_payload,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": self.aggregate_id,
            "aggregate_version": self.aggregate_version,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "command_id": self.command_id,
            "action": self.action.value,
            "risk_effect": self.risk_effect.value,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "economic_order_id": self.economic_order_id,
            "economic_fingerprint": self.economic_fingerprint,
            "request_fingerprint": self.request_fingerprint,
            "tenant_id": self.tenant_id,
            "credential_id": self.credential_id,
            "account_scope": self.account_scope,
            "instrument_id": self.instrument_id,
            "market_type": self.market_type,
            "actor_type": self.actor_type.value,
            "actor_id": self.actor_id,
            "source": self.source.value,
            "mode": self.mode.value,
            "correlation_id": self.correlation_id,
            "occurred_at": self.occurred_at.isoformat(),
            "risk_decision_id": self.risk_decision_id,
            "risk_decision_status": self.risk_decision_status,
            "decision_fingerprint": self.decision_fingerprint,
            "scope_fingerprint": self.scope_fingerprint,
            "audit_fingerprint": self.audit_fingerprint,
            "reservation_id": self.reservation_id,
        }
        canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def map_admission_outbox_to_projection(event: OutboxEvent) -> CandidateProjectionFacts:
    """Parse and losslessly map one Admission Outbox V2 event.

    Unknown schemas, malformed payloads, hash mismatches, and unsupported
    action facts are rejected before a projection fact can be constructed.
    """

    if not isinstance(event, OutboxEvent):
        raise ProjectionMappingError("projection mapping requires OutboxEvent")
    try:
        fact: AdmissionOutboxEventFactV2 = parse_admission_outbox_event(event)
    except (EntryAdmissionError, OutboxProjectionContractError) as exc:
        raise ProjectionMappingError("admission event is not a canonical projection source") from exc
    subject_kind = (
        ProjectionSubjectKind.CANCEL_TARGET
        if fact.action.value == "CANCEL"
        else ProjectionSubjectKind.ECONOMIC_ORDER
    )
    subject_id = (
        fact.subject.cancel_target_id
        if subject_kind is ProjectionSubjectKind.CANCEL_TARGET
        else fact.subject.economic_order_id
    )
    return CandidateProjectionFacts(
        mapping_contract_version=PROJECTION_MAPPING_CONTRACT_VERSION,
        event_id=event.event_id,
        payload_hash=event.payload_hash,
        canonical_payload=event.canonical_payload,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        event_type=event.event_type,
        schema_version=event.schema_version,
        command_id=fact.command_id,
        action=fact.action,
        risk_effect=fact.risk_effect,
        subject_kind=subject_kind,
        subject_id=subject_id,
        economic_order_id=fact.economic_order_id,
        economic_fingerprint=fact.economic_fingerprint,
        request_fingerprint=fact.request_fingerprint,
        tenant_id=fact.tenant_id,
        credential_id=fact.credential_id,
        account_scope=fact.account_scope,
        instrument_id=fact.instrument_id,
        market_type=fact.market_type,
        actor_type=fact.actor_type,
        actor_id=fact.actor_id,
        source=fact.source,
        mode=fact.mode,
        correlation_id=fact.correlation_id,
        occurred_at=fact.occurred_at,
        risk_decision_id=fact.risk_decision_id,
        risk_decision_status=fact.risk_decision_status,
        decision_fingerprint=fact.decision_fingerprint,
        scope_fingerprint=fact.scope_fingerprint,
        audit_fingerprint=fact.audit_fingerprint,
        reservation_id=fact.reservation_id,
    )


__all__ = [
    "CandidateProjectionFacts",
    "PROJECTION_MAPPING_CONTRACT_VERSION",
    "ProjectionMappingError",
    "ProjectionSubjectKind",
    "map_admission_outbox_to_projection",
]
