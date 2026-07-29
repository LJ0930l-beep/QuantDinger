"""Caller-owned adapters for Canonical Entry V2 admission.

This module only composes already-typed durable-entry, durable-risk, and
transactional-outbox boundaries.  It owns neither a database transaction nor
any runtime, routing, worker, exchange, or order-submission behaviour.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2, EconomicOrderSubject
from app.domain.durable_entry_persistence_contracts import DurableEntryPersistResult
from app.domain.durable_risk_enforcement_v2_contracts import (
    DurableRiskEnforcementV2Error,
    DurableRiskPersistResultV2,
    DurableRiskRepositoryError,
    build_durable_risk_facts_v2,
)
from app.domain.entry_admission_v2_contracts import (
    DurableRiskAdmissionInputs,
    EntryAdmissionConflict,
    EntryAdmissionError,
    deterministic_admission_outbox_event,
    require_durable_entry_receipt,
    require_durable_risk_receipt,
)
from app.domain.order_contracts import OrderAction, RiskEffect
from app.domain.outbox_projection_contracts import OutboxConflict, OutboxProjectionContractError
from app.services.durable_risk_enforcement_v2_repository import DurableRiskEnforcementRepositoryV2
from app.services.outbox_projection_repository import (
    OutboxPersistResult,
    OutboxProjectionRepository,
    OutboxRepositoryError,
    outbox_event_fingerprint,
)


class DurableRiskAdmissionProvider(Protocol):
    """Supplies already-observed, immutable hard-risk inputs for one graph."""

    def prepare(self, graph: DurableEntryGraphV2) -> DurableRiskAdmissionInputs: ...


class DurableRiskAdmissionAdapter:
    """Build and persist one V2 hard-risk decision without transaction control."""

    def __init__(
        self,
        *,
        provider: DurableRiskAdmissionProvider,
        repository: DurableRiskEnforcementRepositoryV2 | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository or DurableRiskEnforcementRepositoryV2()

    def evaluate_and_persist(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
    ) -> DurableRiskPersistResultV2:
        if not isinstance(graph, DurableEntryGraphV2):
            raise EntryAdmissionError("durable risk adapter requires DurableEntryGraphV2")
        specification = graph.specification
        if specification.action is OrderAction.CANCEL:
            raise EntryAdmissionConflict("CANCEL must bypass durable hard risk")
        if not isinstance(graph.subject, EconomicOrderSubject):
            raise EntryAdmissionConflict("non-CANCEL risk admission requires an economic order")

        inputs = self._provider.prepare(graph)
        if not isinstance(inputs, DurableRiskAdmissionInputs):
            raise EntryAdmissionError("durable risk provider returned untyped admission inputs")

        increasing = specification.action in (OrderAction.OPEN, OrderAction.INCREASE)
        if not increasing and inputs.reservation_demand is not None:
            raise EntryAdmissionConflict("reducing risk admission cannot carry a reservation demand")

        # Evaluate first without a reservation.  A denied decision must never
        # manufacture or persist a reservation, even if a provider calculated
        # a possible demand before evaluating the hard-risk policy.
        policy, input_snapshot, decision, _ = build_durable_risk_facts_v2(
            graph,
            policy=inputs.policy,
            exposure=inputs.exposure,
            kill_switches=inputs.kill_switches,
            request=inputs.request,
            observed_at=inputs.observed_at,
            active_reservations=inputs.active_reservations,
        )
        reservation = None
        if decision.decision.allowed and increasing:
            if inputs.reservation_demand is None:
                raise EntryAdmissionConflict("allowed OPEN/INCREASE requires a typed reservation demand")
            policy, input_snapshot, decision, reservation = build_durable_risk_facts_v2(
                graph,
                policy=inputs.policy,
                exposure=inputs.exposure,
                kill_switches=inputs.kill_switches,
                request=inputs.request,
                observed_at=inputs.observed_at,
                active_reservations=inputs.active_reservations,
                reservation_demand=inputs.reservation_demand,
                expires_at=inputs.expires_at,
            )

        try:
            result = self._repository.persist_durable_risk(
                connection,
                policy_snapshot=policy,
                input_snapshot=input_snapshot,
                decision=decision,
                reservation=reservation,
            )
        except (DurableRiskEnforcementV2Error, DurableRiskRepositoryError):
            raise
        except Exception as exc:
            raise EntryAdmissionError("durable risk persistence failed") from exc

        result = require_durable_risk_receipt(result, graph)
        if result.allowed != decision.decision.allowed or result.decision_status != decision.decision_status:
            raise EntryAdmissionConflict("durable risk receipt conflicts with evaluated decision")
        if increasing and result.allowed and not result.reservation_id:
            raise EntryAdmissionConflict("allowed OPEN/INCREASE requires a durable reservation receipt")
        if (not increasing or not result.allowed) and result.reservation_id is not None:
            raise EntryAdmissionConflict("non-reserving risk outcome returned a reservation receipt")
        return result


class AdmissionOutboxAdapter:
    """Persist one deterministic V2 admission event without transaction control."""

    def __init__(self, *, repository: OutboxProjectionRepository | None = None) -> None:
        self._repository = repository or OutboxProjectionRepository()

    def persist_admission(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
        durable_result: DurableEntryPersistResult,
        risk_result: DurableRiskPersistResultV2 | None,
    ) -> OutboxPersistResult:
        if not isinstance(graph, DurableEntryGraphV2):
            raise EntryAdmissionError("outbox adapter requires DurableEntryGraphV2")
        require_durable_entry_receipt(durable_result, graph)
        specification = graph.specification
        if specification.action is OrderAction.CANCEL:
            if risk_result is not None:
                raise EntryAdmissionConflict("CANCEL must not emit a durable risk receipt")
        else:
            if risk_result is None:
                raise EntryAdmissionConflict("non-CANCEL admission requires a durable risk receipt")
            require_durable_risk_receipt(risk_result, graph)
            if risk_result.allowed and specification.risk_effect is RiskEffect.INCREASE_RISK and not risk_result.reservation_id:
                raise EntryAdmissionConflict("allowed OPEN/INCREASE outbox requires reservation receipt")
            if (not risk_result.allowed or specification.risk_effect is not RiskEffect.INCREASE_RISK) and risk_result.reservation_id is not None:
                raise EntryAdmissionConflict("outbox admission received an impermissible reservation receipt")

        event = deterministic_admission_outbox_event(graph, risk_result=risk_result)
        # The existing repository is the only database boundary and owns its
        # typed driver-error conversion.  This adapter must not conceal an
        # OutboxConflict or change caller-owned transaction behaviour.
        try:
            result = self._repository.persist_event(
                connection,
                event,
                available_at=specification.occurred_at,
            )
        except (OutboxRepositoryError, OutboxConflict, OutboxProjectionContractError):
            raise
        except Exception as exc:
            raise EntryAdmissionError("admission outbox persistence failed") from exc
        if not isinstance(result, OutboxPersistResult):
            raise EntryAdmissionConflict("outbox port returned an untyped receipt")
        if result.event != event:
            raise EntryAdmissionConflict("outbox receipt conflicts with deterministic admission event")
        # Require the existing shared fingerprint calculation; this adapter
        # intentionally has no parallel event identity algorithm.
        if outbox_event_fingerprint(result.event) != outbox_event_fingerprint(event):
            raise EntryAdmissionConflict("outbox receipt fingerprint conflicts with admission event")
        return result
