"""Pure, caller-owned contracts for Canonical Entry V2 admission.

This is the single agreement between the admission gateway and its durable
entry, durable-risk, and transactional-outbox adapters.  It deliberately has
no runtime, database, worker, route, or exchange dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
from uuid import UUID

from app.domain.canonical_entry_v2_contracts import (
    CancelTargetKind,
    CancelTargetSubject,
    DurableEntryGraphV2,
    EconomicOrderSubject,
)
from app.domain.durable_entry_persistence_contracts import DurableEntryPersistResult
from app.domain.durable_entry_persistence_contracts import DURABLE_ENTRY_CONTRACT_VERSION
from app.domain.durable_risk_enforcement_v2_contracts import DurableRiskPersistResultV2
from app.domain.hard_risk_contracts import (
    HardRiskRequest,
    KillSwitchSnapshot,
    RiskExposureSnapshot,
    RiskLimitPolicy,
    RiskReservationDemand,
)
from app.domain.canonical_entry_contracts import EntryMode, EntrySource
from app.domain.order_contracts import Actor, OrderAction, RiskEffect
from app.domain.outbox_projection_contracts import OutboxEvent, canonical_payload_json


ENTRY_ADMISSION_V2_CONTRACT_VERSION = "entry-admission-v2"
ENTRY_ADMISSION_OUTBOX_SCHEMA_VERSION = "entry-admission-v2"
ENTRY_ADMISSION_EVENT_TYPE = "DURABLE_ENTRY_ADMITTED"
ENTRY_ADMISSION_CANCEL_EVENT_TYPE = "DURABLE_CANCEL_ADMITTED"
ENTRY_ADMISSION_ECONOMIC_ORDER_AGGREGATE = "DURABLE_ECONOMIC_ORDER"
ENTRY_ADMISSION_COMMAND_AGGREGATE = "DURABLE_ENTRY_COMMAND"
ENTRY_ADMISSION_SUPPORTED_SCHEMAS = frozenset({
    (ENTRY_ADMISSION_EVENT_TYPE, ENTRY_ADMISSION_OUTBOX_SCHEMA_VERSION),
    (ENTRY_ADMISSION_CANCEL_EVENT_TYPE, ENTRY_ADMISSION_OUTBOX_SCHEMA_VERSION),
})


class EntryAdmissionError(ValueError):
    """A typed Canonical Entry V2 admission contract violation."""


class EntryAdmissionConflict(EntryAdmissionError):
    """A port receipt conflicts with immutable Canonical Entry V2 facts."""


class EntryAdmissionDisposition(str, Enum):
    DISABLED = "DISABLED"
    RISK_REJECTED = "RISK_REJECTED"
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


AdmissionSubject = EconomicOrderSubject | CancelTargetSubject

_ENTRY_ACTIONS = frozenset({
    OrderAction.OPEN,
    OrderAction.INCREASE,
    OrderAction.REDUCE,
    OrderAction.CLOSE,
    OrderAction.EMERGENCY_CLOSE,
    OrderAction.PROTECTION,
})
_REDUCING_ACTIONS = frozenset({
    OrderAction.REDUCE,
    OrderAction.CLOSE,
    OrderAction.EMERGENCY_CLOSE,
    OrderAction.PROTECTION,
})
_ADMISSION_PAYLOAD_KEYS = frozenset({
    "admission_contract_version",
    "command_id",
    "action",
    "risk_effect",
    "subject_kind",
    "subject_id",
    "cancel_target_kind",
    "economic_order_id",
    "economic_fingerprint",
    "request_fingerprint",
    "tenant_id",
    "credential_id",
    "account_scope",
    "instrument_id",
    "market_type",
    "actor_type",
    "actor_id",
    "source",
    "mode",
    "correlation_id",
    "occurred_at",
    "risk_decision_id",
    "risk_decision_status",
    "decision_fingerprint",
    "scope_fingerprint",
    "audit_fingerprint",
    "reservation_id",
})


def _uuid_text(value: object, name: str) -> str:
    try:
        return str(UUID(str(value))).lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise EntryAdmissionError(f"{name} must be a UUID") from exc


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise EntryAdmissionError(f"{name} must be canonical ASCII text")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EntryAdmissionError(f"{name} must be a positive integer")
    return value


def _fingerprint(value: object, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise EntryAdmissionError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _optional_uuid(value: object, name: str) -> str | None:
    return None if value is None else _uuid_text(value, name)


def _optional_fingerprint(value: object, name: str) -> str | None:
    return None if value is None else _fingerprint(value, name)


def _occurred_at(value: object) -> datetime:
    if not isinstance(value, str):
        raise EntryAdmissionError("occurred_at must be an ISO-8601 UTC string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EntryAdmissionError("occurred_at must be an ISO-8601 UTC string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EntryAdmissionError("occurred_at must use a zero UTC offset")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class AdmissionOutboxEventFactV2:
    """A complete, replay-stable Admission Outbox event ready for a future reader.

    This is intentionally only a typed parse result.  It is not a projection
    consumer and cannot create a generation, compare shadow state, or derive
    reconciliation health.
    """

    event: OutboxEvent
    command_id: str
    action: OrderAction
    risk_effect: RiskEffect
    subject: AdmissionSubject
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


@dataclass(frozen=True, slots=True)
class DurableRiskAdmissionInputs:
    """Injected, already-observed risk facts; no runtime provider is implied."""

    policy: RiskLimitPolicy
    exposure: RiskExposureSnapshot
    kill_switches: KillSwitchSnapshot
    request: HardRiskRequest
    observed_at: datetime
    active_reservations: tuple[RiskReservationDemand, ...] = ()
    reservation_demand: RiskReservationDemand | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.active_reservations, tuple) or not all(
            isinstance(item, RiskReservationDemand) for item in self.active_reservations
        ):
            raise EntryAdmissionError("active_reservations must be typed immutable demands")
        if self.reservation_demand is not None and not isinstance(
            self.reservation_demand, RiskReservationDemand
        ):
            raise EntryAdmissionError("reservation_demand must be a typed demand")


@dataclass(frozen=True, slots=True)
class EntryAdmissionResultV2:
    disposition: EntryAdmissionDisposition
    mode: object
    command_id: str
    action: OrderAction
    subject: AdmissionSubject
    economic_order_id: str | None
    economic_fingerprint: str
    request_fingerprint: str
    risk_decision_id: str | None = None
    risk_decision_status: str | None = None
    reservation_id: str | None = None
    outbox_event_id: str | None = None
    outbox_payload_hash: str | None = None
    outbox_event_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, EntryAdmissionDisposition):
            raise EntryAdmissionError("disposition must be typed")
        if not isinstance(self.action, OrderAction):
            raise EntryAdmissionError("action must be typed")
        if not isinstance(self.subject, (EconomicOrderSubject, CancelTargetSubject)):
            raise EntryAdmissionError("subject must be typed")
        if not all(isinstance(value, str) and value for value in (
            self.command_id, self.economic_fingerprint, self.request_fingerprint,
        )):
            raise EntryAdmissionError("admission identity must be canonical text")
        if self.action is OrderAction.CANCEL:
            if not isinstance(self.subject, CancelTargetSubject) or self.economic_order_id is not None:
                raise EntryAdmissionError("CANCEL admission must use only a cancel subject")
        elif not isinstance(self.subject, EconomicOrderSubject):
            raise EntryAdmissionError("non-CANCEL admission requires an economic order subject")


def _receipt_matches_graph(
    receipt: DurableEntryPersistResult,
    graph: DurableEntryGraphV2,
) -> bool:
    specification = graph.specification
    expected_order_id = (
        graph.subject.economic_order_id
        if isinstance(graph.subject, EconomicOrderSubject)
        else None
    )
    return isinstance(receipt, DurableEntryPersistResult) and (
        receipt.command_id,
        receipt.action,
        receipt.subject,
        receipt.economic_order_id,
        receipt.economic_fingerprint,
        receipt.request_fingerprint,
    ) == (
        graph.command_id,
        specification.action,
        graph.subject,
        expected_order_id,
        specification.economic_fingerprint,
        specification.request_fingerprint,
    )


def require_durable_entry_receipt(
    receipt: DurableEntryPersistResult,
    graph: DurableEntryGraphV2,
) -> DurableEntryPersistResult:
    if not _receipt_matches_graph(receipt, graph):
        raise EntryAdmissionConflict("durable entry receipt conflicts with graph identity")
    return receipt


def require_durable_risk_receipt(
    receipt: DurableRiskPersistResultV2,
    graph: DurableEntryGraphV2,
) -> DurableRiskPersistResultV2:
    """Verify every V2 scope and audit identity without recalculating it."""

    if not isinstance(receipt, DurableRiskPersistResultV2):
        raise EntryAdmissionConflict("durable risk port returned an untyped receipt")
    if not isinstance(graph.subject, EconomicOrderSubject):
        raise EntryAdmissionConflict("CANCEL must not produce a durable risk receipt")
    specification = graph.specification
    expected = (
        graph.command_id,
        graph.subject.economic_order_id,
        DURABLE_ENTRY_CONTRACT_VERSION,
        specification.economic_fingerprint,
        specification.request_fingerprint,
        specification.tenant_id,
        specification.credential_id,
        specification.account_scope,
        specification.instrument_id,
        specification.market_type,
        specification.action,
        specification.risk_effect,
        specification.actor.actor_type.value,
        specification.actor.actor_id,
        specification.actor.entry_source.value,
        specification.mode.value,
        specification.correlation_id,
        specification.occurred_at,
    )
    actual = (
        receipt.command_id,
        receipt.economic_order_id,
        receipt.durable_entry_contract_version,
        receipt.economic_fingerprint,
        receipt.request_fingerprint,
        receipt.tenant_id,
        receipt.credential_id,
        receipt.account_scope,
        receipt.instrument_id,
        receipt.market_type,
        receipt.action,
        receipt.risk_effect,
        receipt.actor_type,
        receipt.actor_id,
        receipt.source,
        receipt.mode,
        receipt.correlation_id,
        receipt.entry_occurred_at,
    )
    if actual != expected or not receipt.scope_fingerprint or not receipt.audit_fingerprint:
        raise EntryAdmissionConflict("durable risk receipt conflicts with graph identity")
    return receipt


def deterministic_admission_outbox_event(
    graph: DurableEntryGraphV2,
    *,
    risk_result: DurableRiskPersistResultV2 | None,
) -> OutboxEvent:
    """Build one replay-stable admission event from immutable V2 receipts.

    Persistence dispositions and database/current time are intentionally absent:
    they change between first write and exact replay.
    """

    specification = graph.specification
    if specification.action is OrderAction.CANCEL:
        if risk_result is not None or not isinstance(graph.subject, CancelTargetSubject):
            raise EntryAdmissionConflict("CANCEL outbox event cannot carry durable risk")
        aggregate_type = ENTRY_ADMISSION_COMMAND_AGGREGATE
        aggregate_id = graph.command_id
        event_type = ENTRY_ADMISSION_CANCEL_EVENT_TYPE
        economic_order_id = None
    else:
        if not isinstance(graph.subject, EconomicOrderSubject):
            raise EntryAdmissionConflict("non-CANCEL outbox event requires economic order subject")
        aggregate_type = ENTRY_ADMISSION_ECONOMIC_ORDER_AGGREGATE
        aggregate_id = graph.subject.economic_order_id
        event_type = ENTRY_ADMISSION_EVENT_TYPE
        economic_order_id = graph.subject.economic_order_id

    payload = {
        "admission_contract_version": ENTRY_ADMISSION_V2_CONTRACT_VERSION,
        "command_id": graph.command_id,
        "action": specification.action.value,
        "risk_effect": specification.risk_effect.value,
        "subject_kind": (
            "CANCEL_TARGET" if isinstance(graph.subject, CancelTargetSubject) else "ECONOMIC_ORDER"
        ),
        "cancel_target_kind": (
            graph.subject.cancel_target_kind.value
            if isinstance(graph.subject, CancelTargetSubject)
            else None
        ),
        "subject_id": (
            graph.subject.cancel_target_id
            if isinstance(graph.subject, CancelTargetSubject)
            else graph.subject.economic_order_id
        ),
        "economic_order_id": economic_order_id,
        "economic_fingerprint": specification.economic_fingerprint,
        "request_fingerprint": specification.request_fingerprint,
        "tenant_id": specification.tenant_id,
        "credential_id": specification.credential_id,
        "account_scope": specification.account_scope,
        "instrument_id": specification.instrument_id,
        "market_type": specification.market_type,
        "actor_type": specification.actor.actor_type.value,
        "actor_id": specification.actor.actor_id,
        "source": specification.actor.entry_source.value,
        "mode": specification.mode.value,
        "correlation_id": specification.correlation_id,
        "occurred_at": specification.occurred_at.isoformat(),
        "risk_decision_id": None if risk_result is None else risk_result.decision_id,
        "risk_decision_status": None if risk_result is None else risk_result.decision_status,
        "decision_fingerprint": None if risk_result is None else risk_result.decision_fingerprint,
        "scope_fingerprint": None if risk_result is None else risk_result.scope_fingerprint,
        "audit_fingerprint": None if risk_result is None else risk_result.audit_fingerprint,
        "reservation_id": None if risk_result is None else risk_result.reservation_id,
    }
    return OutboxEvent(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=0,
        event_type=event_type,
        schema_version=ENTRY_ADMISSION_OUTBOX_SCHEMA_VERSION,
        payload=payload,
    )


def parse_admission_outbox_event(event: OutboxEvent) -> AdmissionOutboxEventFactV2:
    """Fail closed unless an event exactly matches the Admission V2 schema.

    The parser validates immutable event identity, payload hash, typed scope,
    action matrix, risk facts, and reservation facts.  It performs no I/O and
    is deliberately independent of any projection or runtime consumer.
    """

    if not isinstance(event, OutboxEvent):
        raise EntryAdmissionError("admission event parser requires OutboxEvent")
    if (event.event_type, event.schema_version) not in ENTRY_ADMISSION_SUPPORTED_SCHEMAS:
        raise EntryAdmissionError("outbox event schema is not registered for admission")
    canonical_payload = canonical_payload_json(event.payload)
    if event.canonical_payload != canonical_payload:
        raise EntryAdmissionError("outbox canonical payload is inconsistent")
    if event.payload_hash != hashlib.sha256(canonical_payload.encode("ascii")).hexdigest():
        raise EntryAdmissionError("outbox payload hash is inconsistent")
    payload = dict(event.payload)
    if frozenset(payload) != _ADMISSION_PAYLOAD_KEYS:
        raise EntryAdmissionError("admission payload keys are incomplete or unknown")
    if payload["admission_contract_version"] != ENTRY_ADMISSION_V2_CONTRACT_VERSION:
        raise EntryAdmissionError("unsupported admission contract version")

    command_id = _uuid_text(payload["command_id"], "command_id")
    if event.event_type == ENTRY_ADMISSION_CANCEL_EVENT_TYPE:
        action = OrderAction.CANCEL
    else:
        try:
            action = OrderAction(payload["action"])
        except (TypeError, ValueError) as exc:
            raise EntryAdmissionError("payload action must use OrderAction") from exc
    if payload["action"] != action.value:
        raise EntryAdmissionError("payload action conflicts with event type")
    try:
        risk_effect = RiskEffect(payload["risk_effect"])
    except (TypeError, ValueError) as exc:
        raise EntryAdmissionError("payload risk_effect must use RiskEffect") from exc

    subject_kind = payload["subject_kind"]
    subject_id = payload["subject_id"]
    if action is OrderAction.CANCEL:
        if event.aggregate_type != ENTRY_ADMISSION_COMMAND_AGGREGATE or event.aggregate_id != command_id:
            raise EntryAdmissionError("CANCEL aggregate must name its command")
        if event.aggregate_version != 0 or risk_effect is not RiskEffect.NEUTRAL:
            raise EntryAdmissionError("CANCEL event has invalid aggregate or risk facts")
        if subject_kind != "CANCEL_TARGET":
            raise EntryAdmissionError("CANCEL event requires a cancel target subject")
        try:
            cancel_kind = CancelTargetKind(payload["cancel_target_kind"])
        except (TypeError, ValueError) as exc:
            raise EntryAdmissionError("CANCEL event has an invalid target kind") from exc
        subject = CancelTargetSubject(cancel_kind, subject_id)
        if any(payload[name] is not None for name in (
            "economic_order_id", "risk_decision_id", "risk_decision_status",
            "decision_fingerprint", "scope_fingerprint", "audit_fingerprint",
            "reservation_id",
        )):
            raise EntryAdmissionError("CANCEL event cannot carry risk or economic-order facts")
        economic_order_id = None
        risk_decision_id = None
        risk_decision_status = None
        decision_fingerprint = None
        scope_fingerprint = None
        audit_fingerprint = None
        reservation_id = None
    else:
        if action not in _ENTRY_ACTIONS:
            raise EntryAdmissionError("entry admission event has an unsupported action")
        if event.aggregate_type != ENTRY_ADMISSION_ECONOMIC_ORDER_AGGREGATE or event.aggregate_version != 0:
            raise EntryAdmissionError("entry admission event has an invalid aggregate")
        if subject_kind != "ECONOMIC_ORDER" or payload["cancel_target_kind"] is not None:
            raise EntryAdmissionError("entry admission event requires an economic order subject")
        subject = EconomicOrderSubject(subject_id)
        economic_order_id = _uuid_text(payload["economic_order_id"], "economic_order_id")
        if subject.economic_order_id != economic_order_id or event.aggregate_id != economic_order_id:
            raise EntryAdmissionError("entry admission aggregate and subject must match economic order")
        expected_effect = (
            RiskEffect.INCREASE_RISK
            if action in {OrderAction.OPEN, OrderAction.INCREASE}
            else RiskEffect.REDUCE_RISK
        )
        if risk_effect is not expected_effect:
            raise EntryAdmissionError("entry admission risk effect conflicts with action")
        risk_decision_id = _uuid_text(payload["risk_decision_id"], "risk_decision_id")
        risk_decision_status = _text(payload["risk_decision_status"], "risk_decision_status")
        decision_fingerprint = _fingerprint(payload["decision_fingerprint"], "decision_fingerprint")
        scope_fingerprint = _fingerprint(payload["scope_fingerprint"], "scope_fingerprint")
        audit_fingerprint = _fingerprint(payload["audit_fingerprint"], "audit_fingerprint")
        reservation_id = _optional_uuid(payload["reservation_id"], "reservation_id")
        if risk_decision_status != "ALLOW":
            raise EntryAdmissionError("admitted non-CANCEL event requires an ALLOW decision")
        if action in {OrderAction.OPEN, OrderAction.INCREASE}:
            if reservation_id is None:
                raise EntryAdmissionError("admitted OPEN/INCREASE requires a reservation")
        elif reservation_id is not None:
            raise EntryAdmissionError("admitted reducing action cannot carry a reservation")

    try:
        actor_type = Actor(payload["actor_type"])
        source = EntrySource(payload["source"])
        mode = EntryMode(payload["mode"])
    except (TypeError, ValueError) as exc:
        raise EntryAdmissionError("admission audit facts must use typed enums") from exc
    fact = AdmissionOutboxEventFactV2(
        event=event,
        command_id=command_id,
        action=action,
        risk_effect=risk_effect,
        subject=subject,
        economic_order_id=economic_order_id,
        economic_fingerprint=_fingerprint(payload["economic_fingerprint"], "economic_fingerprint"),
        request_fingerprint=_fingerprint(payload["request_fingerprint"], "request_fingerprint"),
        tenant_id=_positive_int(payload["tenant_id"], "tenant_id"),
        credential_id=_positive_int(payload["credential_id"], "credential_id"),
        account_scope=_text(payload["account_scope"], "account_scope"),
        instrument_id=_text(payload["instrument_id"], "instrument_id"),
        market_type=_text(payload["market_type"], "market_type"),
        actor_type=actor_type,
        actor_id=_text(payload["actor_id"], "actor_id"),
        source=source,
        mode=mode,
        correlation_id=_text(payload["correlation_id"], "correlation_id"),
        occurred_at=_occurred_at(payload["occurred_at"]),
        risk_decision_id=risk_decision_id,
        risk_decision_status=risk_decision_status,
        decision_fingerprint=decision_fingerprint,
        scope_fingerprint=scope_fingerprint,
        audit_fingerprint=audit_fingerprint,
        reservation_id=reservation_id,
    )
    # Reconstructing the immutable event also validates its deterministic ID.
    expected = OutboxEvent(
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_version=event.aggregate_version,
        event_type=event.event_type,
        schema_version=event.schema_version,
        payload=payload,
    )
    if expected != event:
        raise EntryAdmissionError("outbox event identity is inconsistent")
    return fact
