"""Explicit, opt-in TestNet execution seam for an already-admitted entry.

This is not a scheduler and is not registered by application startup.  It
consumes a typed Admission result, validates the same canonical graph facts,
then delegates one request to an injected Gate TestNet client.  Persistence is
optional but, when supplied, remains caller-owned and uses the immutable
fill-ledger bridge on the same connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from app.domain.canonical_entry_v2_contracts import DurableEntryGraphV2, EconomicOrderSubject
from app.domain.canonical_entry_contracts import EntryMode
from app.domain.entry_admission_v2_contracts import EntryAdmissionDisposition, EntryAdmissionResultV2
from app.domain.gate_testnet_execution_contracts import GateTestnetExecutionReceipt, GateTestnetExecutionRequest
from app.domain.order_contracts import Actor, OrderAction, RiskEffect, SubmissionAttemptState
from app.domain.order_state_machine import (
    SubmissionAttemptScope,
    TransitionCause,
    authorize_attempt_transition,
)
from app.services.order_state_repository import OrderStateRepository
from app.domain.gate_testnet_ledger_contracts import GateTestnetLedgerScope
from app.domain.immutable_fill_ledger import FillLedgerInput
from app.services.gate_testnet_ledger_persistence_service import (
    GateTestnetLedgerPersistenceResult,
    persist_gate_testnet_receipt_caller_owned,
)
from app.services.gate_testnet_order_client import GateTestnetOrderReceipt
from app.services.immutable_fill_ledger_repository import FillLedgerPersistenceScope
from app.services.submission_attempt_repository import (
    SubmissionAttemptCreateFacts,
    SubmissionAttemptDisposition,
    SubmissionAttemptRepository,
)
from app.services.exchange_order_repository import (
    ExchangeOrderRepository,
    facts_from_gate_receipt,
)


class GateTestnetExecutionError(RuntimeError):
    """Execution was not authorized by a complete typed admission receipt."""


class GateTestnetClientPort(Protocol):
    def submit(self, request: GateTestnetExecutionRequest) -> GateTestnetOrderReceipt: ...


@dataclass(frozen=True, slots=True)
class GateTestnetExecutionResult:
    receipt: GateTestnetOrderReceipt
    ledger: GateTestnetLedgerPersistenceResult | None
    live_enabled: bool = False


# Keep the worker patchable at the instance level for deterministic contract
# tests and dependency-injected adapters.  The worker owns no mutable state
# beyond its injected client and enablement flag, so the regular dataclass
# layout is sufficient here.
@dataclass
class GateTestnetExecutionWorker:
    client: GateTestnetClientPort
    enabled: bool = False

    def execute(
        self,
        connection: object,
        graph: DurableEntryGraphV2,
        admission: EntryAdmissionResultV2,
        request: GateTestnetExecutionRequest,
        *,
        ledger_scope: GateTestnetLedgerScope | None = None,
        persistence_scope: FillLedgerPersistenceScope | None = None,
        ledger_repository: object | None = None,
        attempt_facts: SubmissionAttemptCreateFacts | None = None,
        attempt_repository: SubmissionAttemptRepository | None = None,
        exchange_order_repository: ExchangeOrderRepository | None = None,
        state_repository: OrderStateRepository | None = None,
    ) -> GateTestnetExecutionResult:
        self._validate(graph, admission, request)
        if not self.enabled:
            raise GateTestnetExecutionError("Gate TestNet execution worker is disabled")
        if not isinstance(attempt_facts, SubmissionAttemptCreateFacts) or not isinstance(
            attempt_repository, SubmissionAttemptRepository
        ):
            raise GateTestnetExecutionError(
                "TestNet submission requires a durable Submission Attempt before network I/O"
            )
        if not isinstance(exchange_order_repository, ExchangeOrderRepository):
            raise GateTestnetExecutionError(
                "TestNet submission requires caller-owned exchange-order persistence"
            )
        states = state_repository or OrderStateRepository()
        attempt_result = attempt_repository.persist_caller_owned(connection, attempt_facts)
        if attempt_result.disposition is SubmissionAttemptDisposition.REPLAYED:
            raise GateTestnetExecutionError(
                "existing Submission Attempt requires query/recovery before another submission"
            )
        self._apply_attempt_transition(
            connection, states, attempt_facts, SubmissionAttemptState.READY,
            SubmissionAttemptState.SUBMITTING, request, "SUBMITTING",
        )
        try:
            receipt = self.client.submit(request)
        except Exception as exc:
            self._mark_unknown(connection, states, attempt_facts, request, exc)
            raise GateTestnetExecutionError("Gate TestNet submission failed") from exc
        if not isinstance(receipt, GateTestnetOrderReceipt):
            self._mark_unknown(connection, states, attempt_facts, request, None)
            raise GateTestnetExecutionError("Gate client returned an untyped receipt")
        try:
            exchange_facts = facts_from_gate_receipt(
                receipt,
                request,
                scope=attempt_facts.scope,
                attempt_id=attempt_facts.id,
                attempt_row_id=str(uuid5(NAMESPACE_URL, f"exchange-order:{attempt_facts.id}")),
            )
            exchange_order_repository.persist_caller_owned(connection, exchange_facts)
            self._apply_attempt_transition(
                connection, states, attempt_facts, SubmissionAttemptState.SUBMITTING,
                SubmissionAttemptState.ACKED, request, "ACKED",
            )
        except Exception as exc:
            self._mark_unknown(connection, states, attempt_facts, request, exc)
            raise GateTestnetExecutionError(
                "TestNet response could not be durably recorded; recovery query is required"
            ) from exc
        ledger = None
        if receipt.fills:
            if ledger_scope is None or persistence_scope is None:
                raise GateTestnetExecutionError("filled TestNet receipt requires immutable ledger persistence scopes")
            execution_receipt = getattr(receipt, "execution_receipt", None)
            if execution_receipt is None:
                raise GateTestnetExecutionError(
                    "filled order receipt requires a typed lifecycle receipt before ledger persistence"
                )
            ledger = persist_gate_testnet_receipt_caller_owned(
                connection,
                execution_receipt,
                ledger_scope=ledger_scope,
                persistence_scope=persistence_scope,
                repository=ledger_repository,
            )
        return GateTestnetExecutionResult(receipt, ledger, False)

    @staticmethod
    def _apply_attempt_transition(
        connection: object,
        states: OrderStateRepository,
        facts: SubmissionAttemptCreateFacts,
        current: SubmissionAttemptState,
        target: SubmissionAttemptState,
        request: GateTestnetExecutionRequest,
        reason: str,
    ) -> None:
        payload = {
            "attempt_id": facts.id,
            "request_fingerprint": facts.request_fingerprint,
            "target_state": target.value,
            "reason": reason,
        }
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        evidence_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        transition = authorize_attempt_transition(
            aggregate_id=facts.id,
            aggregate_scope=facts.scope,
            current_state=current,
            target_state=target,
            expected_version=0 if current is SubmissionAttemptState.READY else 1,
            cause=TransitionCause.SUBMISSION_RESULT,
            actor=Actor.ADMIN,
            reason_code=reason,
            correlation_id=f"gate-attempt:{facts.id}",
            occurred_at=request.observed_at.astimezone(timezone.utc),
            evidence_hash=evidence_hash,
            canonical_payload=payload,
            idempotency_key=f"gate-attempt:{facts.id}:{target.value}",
        )
        states.apply_attempt_transition_caller_owned(connection, transition)

    @classmethod
    def _mark_unknown(
        cls,
        connection: object,
        states: OrderStateRepository,
        facts: SubmissionAttemptCreateFacts,
        request: GateTestnetExecutionRequest,
        cause: BaseException | None,
    ) -> None:
        try:
            cls._apply_attempt_transition(
                connection, states, facts, SubmissionAttemptState.SUBMITTING,
                SubmissionAttemptState.UNKNOWN, request, "SUBMISSION_UNKNOWN",
            )
        except Exception as transition_error:
            raise GateTestnetExecutionError(
                "submission failed and UNKNOWN state could not be persisted"
            ) from transition_error

    @staticmethod
    def _validate(graph: DurableEntryGraphV2, admission: EntryAdmissionResultV2, request: GateTestnetExecutionRequest) -> None:
        if not isinstance(graph, DurableEntryGraphV2) or not isinstance(admission, EntryAdmissionResultV2):
            raise GateTestnetExecutionError("typed canonical graph and admission result are required")
        if admission.command_id != graph.command_id or admission.action is not graph.specification.action:
            raise GateTestnetExecutionError("admission identity does not match canonical graph")
        # A replay proves that the durable admission facts already exist; it
        # is not a new authorization to submit another economic order.  A
        # caller that needs recovery must query the existing exchange order
        # through the read boundary first, then explicitly construct a
        # recovery operation.  Keeping this seam CREATED-only prevents a
        # retry after a process restart from duplicating a TestNet order.
        if admission.disposition is not EntryAdmissionDisposition.CREATED:
            raise GateTestnetExecutionError("only CREATED admission may submit to TestNet")
        if graph.specification.mode is EntryMode.DISABLED:
            raise GateTestnetExecutionError("DISABLED entry cannot reach TestNet")
        if graph.specification.action is OrderAction.CANCEL:
            raise GateTestnetExecutionError("CANCEL requires a typed cancel client boundary")
        if admission.risk_decision_status != "ALLOW":
            raise GateTestnetExecutionError("TestNet execution requires an ALLOW risk decision")
        if graph.specification.risk_effect is RiskEffect.INCREASE_RISK and not admission.reservation_id:
            raise GateTestnetExecutionError("increasing-risk TestNet execution requires a reservation receipt")
        if graph.specification.risk_effect is RiskEffect.REDUCE_RISK and admission.reservation_id is not None:
            raise GateTestnetExecutionError("reducing-risk TestNet execution cannot carry a reservation")
        if not isinstance(graph.subject, EconomicOrderSubject):
            raise GateTestnetExecutionError("TestNet order requires an economic-order subject")
        intent = graph.specification.economic_intent
        if request.account_scope != graph.specification.account_scope or request.instrument_id != graph.specification.instrument_id:
            raise GateTestnetExecutionError("TestNet request scope does not match canonical graph")
        expected_quantity = intent.quantity or intent.close_quantity
        if expected_quantity is None or intent.close_all or intent.side is None:
            raise GateTestnetExecutionError("TestNet order requires an explicit executable quantity")
        # Gate order facts use lowercase venue values while Canonical Entry V2
        # uses uppercase domain values.  Compare the typed semantic value, not
        # the transport spelling; otherwise every valid BUY/SELL admission is
        # rejected before the TestNet boundary.
        if request.quantity != expected_quantity.value or request.side.value.upper() != intent.side.value.upper():
            raise GateTestnetExecutionError("TestNet request economics do not match canonical graph")


__all__ = ["GateTestnetExecutionError", "GateTestnetExecutionResult", "GateTestnetExecutionWorker"]
