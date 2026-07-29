"""Caller-owned Canonical Entry V2 admission orchestration.

The gateway composes already-typed durable entry, durable-risk V2, and outbox
ports on one caller-provided connection.  It is deliberately not a runtime
entry point and never owns a database transaction or an execution client.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.canonical_entry_contracts import EntryMode
from app.domain.canonical_entry_v2_contracts import (
    CancelTargetSubject,
    DurableEntryGraphV2,
    EconomicOrderSubject,
)
from app.domain.durable_entry_persistence_contracts import (
    DurableEntryPersistDisposition,
    DurableEntryPersistResult,
)
from app.domain.durable_risk_enforcement_v2_contracts import (
    DurableRiskPersistDisposition,
    DurableRiskPersistResultV2,
)
from app.domain.entry_admission_v2_contracts import (
    EntryAdmissionConflict,
    EntryAdmissionDisposition,
    EntryAdmissionError,
    EntryAdmissionResultV2,
    deterministic_admission_outbox_event,
    require_durable_entry_receipt,
    require_durable_risk_receipt,
)
from app.domain.order_contracts import OrderAction, RiskEffect
from app.services.outbox_projection_repository import (
    OutboxPersistDisposition,
    OutboxPersistResult,
    outbox_event_fingerprint,
)


class DurableEntryPort(Protocol):
    def persist_durable_entry(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
    ) -> DurableEntryPersistResult: ...


class DurableRiskPortV2(Protocol):
    def evaluate_and_persist(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
    ) -> DurableRiskPersistResultV2: ...


class AdmissionOutboxPort(Protocol):
    def persist_admission(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
        durable_result: DurableEntryPersistResult,
        risk_result: DurableRiskPersistResultV2 | None,
    ) -> OutboxPersistResult: ...


class CanonicalEntryAdmissionGateway:
    """Admit one typed V2 graph without committing, rolling back, or executing."""

    def __init__(
        self,
        *,
        durable_entries: DurableEntryPort,
        durable_risk: DurableRiskPortV2,
        outbox: AdmissionOutboxPort,
    ) -> None:
        self._durable_entries = durable_entries
        self._durable_risk = durable_risk
        self._outbox = outbox

    def admit(self, connection: object, graph: DurableEntryGraphV2) -> EntryAdmissionResultV2:
        self._validate_graph(graph)
        specification = graph.specification
        if specification.mode is EntryMode.DISABLED:
            return self._result(
                graph,
                EntryAdmissionDisposition.DISABLED,
                risk_result=None,
                outbox_result=None,
            )

        durable_result = self._persist_durable_entry(connection, graph)
        if specification.action is OrderAction.CANCEL:
            outbox_result = self._persist_outbox(connection, graph, durable_result, None)
            return self._result(
                graph,
                self._combined_disposition(durable_result.disposition, outbox_result.disposition),
                risk_result=None,
                outbox_result=outbox_result,
            )

        risk_result = self._persist_durable_risk(connection, graph)
        if not risk_result.allowed:
            if risk_result.decision_status not in {"DENY", "RECONCILIATION_REQUIRED"}:
                raise EntryAdmissionConflict("denied durable risk receipt has an invalid status")
            if risk_result.reservation_id is not None:
                raise EntryAdmissionConflict("denied durable risk receipt cannot reserve capacity")
            return self._result(
                graph,
                EntryAdmissionDisposition.RISK_REJECTED,
                risk_result=risk_result,
                outbox_result=None,
            )

        if risk_result.decision_status != "ALLOW":
            raise EntryAdmissionConflict("allowed durable risk receipt must use ALLOW status")
        if specification.risk_effect is RiskEffect.INCREASE_RISK:
            if not risk_result.reservation_id:
                raise EntryAdmissionConflict("allowed OPEN/INCREASE requires a durable reservation")
        elif specification.risk_effect is RiskEffect.REDUCE_RISK:
            if risk_result.reservation_id is not None:
                raise EntryAdmissionConflict("reducing action cannot reserve capacity")
        else:
            raise EntryAdmissionConflict("non-CANCEL admission has an unsupported risk effect")

        outbox_result = self._persist_outbox(connection, graph, durable_result, risk_result)
        return self._result(
            graph,
            self._combined_disposition(
                durable_result.disposition,
                risk_result.disposition,
                outbox_result.disposition,
            ),
            risk_result=risk_result,
            outbox_result=outbox_result,
        )

    @staticmethod
    def _validate_graph(graph: DurableEntryGraphV2) -> None:
        if not isinstance(graph, DurableEntryGraphV2):
            raise EntryAdmissionError("gateway requires DurableEntryGraphV2")
        specification = graph.specification
        if specification.action is OrderAction.CANCEL:
            if not isinstance(graph.subject, CancelTargetSubject):
                raise EntryAdmissionConflict("CANCEL requires a typed cancel subject")
            if specification.risk_effect is not RiskEffect.NEUTRAL:
                raise EntryAdmissionConflict("CANCEL must be neutral risk")
            return
        if not isinstance(graph.subject, EconomicOrderSubject):
            raise EntryAdmissionConflict("non-CANCEL admission requires an economic-order subject")
        if specification.risk_effect not in {
            RiskEffect.INCREASE_RISK,
            RiskEffect.REDUCE_RISK,
        }:
            raise EntryAdmissionConflict("non-CANCEL admission must have a non-neutral risk effect")

    def _persist_durable_entry(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
    ) -> DurableEntryPersistResult:
        result = self._durable_entries.persist_durable_entry(connection, graph)
        result = require_durable_entry_receipt(result, graph)
        if result.disposition not in {
            DurableEntryPersistDisposition.CREATED,
            DurableEntryPersistDisposition.REPLAYED,
        }:
            raise EntryAdmissionConflict("durable entry receipt has an unsupported disposition")
        return result

    def _persist_durable_risk(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
    ) -> DurableRiskPersistResultV2:
        result = self._durable_risk.evaluate_and_persist(connection, graph)
        result = require_durable_risk_receipt(result, graph)
        if result.disposition not in {
            DurableRiskPersistDisposition.CREATED,
            DurableRiskPersistDisposition.REPLAYED,
        }:
            raise EntryAdmissionConflict("durable risk receipt has an unsupported disposition")
        return result

    def _persist_outbox(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
        durable_result: DurableEntryPersistResult,
        risk_result: DurableRiskPersistResultV2 | None,
    ) -> OutboxPersistResult:
        result = self._outbox.persist_admission(
            connection,
            graph,
            durable_result,
            risk_result,
        )
        expected_event = deterministic_admission_outbox_event(graph, risk_result=risk_result)
        if not isinstance(result, OutboxPersistResult):
            raise EntryAdmissionConflict("admission outbox port returned an untyped receipt")
        if result.disposition not in {
            OutboxPersistDisposition.CREATED,
            OutboxPersistDisposition.REPLAYED,
        }:
            raise EntryAdmissionConflict("admission outbox receipt has an unsupported disposition")
        if result.event != expected_event:
            raise EntryAdmissionConflict("admission outbox receipt conflicts with immutable event facts")
        if outbox_event_fingerprint(result.event) != outbox_event_fingerprint(expected_event):
            raise EntryAdmissionConflict("admission outbox fingerprint conflicts with immutable event facts")
        return result

    @staticmethod
    def _combined_disposition(*dispositions: object) -> EntryAdmissionDisposition:
        replayed = {
            DurableEntryPersistDisposition.REPLAYED,
            DurableRiskPersistDisposition.REPLAYED,
            OutboxPersistDisposition.REPLAYED,
        }
        if all(disposition in replayed for disposition in dispositions):
            return EntryAdmissionDisposition.REPLAYED
        return EntryAdmissionDisposition.CREATED

    @staticmethod
    def _result(
        graph: DurableEntryGraphV2,
        disposition: EntryAdmissionDisposition,
        *,
        risk_result: DurableRiskPersistResultV2 | None,
        outbox_result: OutboxPersistResult | None,
    ) -> EntryAdmissionResultV2:
        specification = graph.specification
        economic_order_id = (
            graph.subject.economic_order_id
            if isinstance(graph.subject, EconomicOrderSubject)
            else None
        )
        return EntryAdmissionResultV2(
            disposition=disposition,
            mode=specification.mode,
            command_id=graph.command_id,
            action=specification.action,
            subject=graph.subject,
            economic_order_id=economic_order_id,
            economic_fingerprint=specification.economic_fingerprint,
            request_fingerprint=specification.request_fingerprint,
            risk_decision_id=None if risk_result is None else risk_result.decision_id,
            risk_decision_status=None if risk_result is None else risk_result.decision_status,
            reservation_id=None if risk_result is None else risk_result.reservation_id,
            outbox_event_id=None if outbox_result is None else outbox_result.event.event_id,
            outbox_payload_hash=None if outbox_result is None else outbox_result.event.payload_hash,
            outbox_event_fingerprint=(
                None if outbox_result is None else outbox_event_fingerprint(outbox_result.event)
            ),
        )
