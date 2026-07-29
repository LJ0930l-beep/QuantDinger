"""Pure, caller-owned contracts for Canonical Entry V2 admission.

This is the single agreement between the admission gateway and its durable
entry, durable-risk, and transactional-outbox adapters.  It deliberately has
no runtime, database, worker, route, or exchange dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.domain.canonical_entry_v2_contracts import (
    CancelTargetSubject,
    DurableEntryGraphV2,
    EconomicOrderSubject,
)
from app.domain.durable_entry_persistence_contracts import DurableEntryPersistResult
from app.domain.durable_risk_enforcement_v2_contracts import DurableRiskPersistResultV2
from app.domain.hard_risk_contracts import (
    HardRiskRequest,
    KillSwitchSnapshot,
    RiskExposureSnapshot,
    RiskLimitPolicy,
    RiskReservationDemand,
)
from app.domain.order_contracts import OrderAction
from app.domain.outbox_projection_contracts import OutboxEvent


ENTRY_ADMISSION_V2_CONTRACT_VERSION = "entry-admission-v2"
ENTRY_ADMISSION_OUTBOX_SCHEMA_VERSION = "entry-admission-v2"
ENTRY_ADMISSION_EVENT_TYPE = "DURABLE_ENTRY_ADMITTED"
ENTRY_ADMISSION_CANCEL_EVENT_TYPE = "DURABLE_CANCEL_ADMITTED"
ENTRY_ADMISSION_ECONOMIC_ORDER_AGGREGATE = "DURABLE_ECONOMIC_ORDER"
ENTRY_ADMISSION_COMMAND_AGGREGATE = "DURABLE_ENTRY_COMMAND"


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
