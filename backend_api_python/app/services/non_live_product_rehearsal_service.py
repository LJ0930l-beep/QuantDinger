"""Deterministic, fixture-only end-to-end product rehearsal.

This service is the safe integration seam for the non-live product.  It
composes Gate public market evidence, the strategy factory, portfolio sizing,
Paper/Shadow simulation, deterministic next-open backtesting, and a derived
Paper position view.  It intentionally has no credential, database, worker,
exchange-write, or LIVE capability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import importlib
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.deterministic_backtest_contracts import BacktestExecutionKind, BacktestRunFacts
from app.domain.backtest_cost_contracts import BacktestCostPolicySnapshot, cost_policy_fingerprint
from app.domain.gate_read_transport_contracts import GatePublicReadEndpoint, GateReadResponse
from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter
from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, GateReadCapabilityProfile
from app.domain.paper_shadow_contracts import PaperShadowRunFacts, SimulationMode
from app.domain.portfolio_risk_contracts import PositionSizingRequest
from app.domain.readonly_paper_account_contracts import (
    PaperOrderStatus,
    ReadonlyPaperAccountSnapshot,
    ReadonlyPaperOrderFact,
)
from app.domain.paper_recovery_contracts import verify_paper_snapshot_recovery
from app.domain.strategy_library_contracts import StrategyDefinition, StrategyFamily, StrategyParameterFact
from app.domain.strategy_library_contracts import SignalDirection, strategy_fingerprint
from app.domain.strategy_signal_candidate_contracts import candidate_from_strategy_signal
from app.domain.canonical_entry_contracts import EntryMode, ExecutionKind
from app.domain.order_contracts import OrderAction
from app.domain.order_state_machine import SubmissionAttemptScope
from app.domain.durable_entry_persistence_contracts import (
    DurableEntryPersistDisposition,
    DurableEntryPersistResult,
    DURABLE_ENTRY_CONTRACT_VERSION,
)
from app.domain.durable_risk_enforcement_v2_contracts import (
    DurableRiskPersistDisposition,
    DurableRiskPersistResultV2,
)
from app.domain.entry_admission_v2_contracts import (
    parse_admission_outbox_event,
    deterministic_admission_outbox_event,
)
from app.domain.gate_testnet_ledger_contracts import (
    GateTestnetLedgerScope,
    build_gate_testnet_ledger_inputs,
)
from app.domain.immutable_fill_ledger import InstrumentAssetScope
from app.domain.gate_testnet_execution_contracts import (
    AssetMarketType as GateExecutionAssetMarketType,
    GateOrderSide as GateExecutionOrderSide,
    GateExecutionKind,
    GateTestnetExecutionRequest,
    GateTriggerDirection,
    GateTriggerPriceType,
    simulate_gate_testnet_execution,
)
from app.domain.canary_release_contracts import CanaryReleaseEvidence, evaluate_canary_promotion
from app.services.entry_admission_gateway import CanonicalEntryAdmissionGateway
from app.services.outbox_projection_repository import OutboxPersistDisposition, OutboxPersistResult
from app.services.builtin_strategy_catalog import builtin_strategy_catalog
from app.services.gate_market_research_service import GateMarketResearchService
from app.services.gate_non_live_research_orchestrator import GateNonLiveResearchOrchestrator
from app.services.gate_testnet_market_session_service import (
    GateTestnetMarketSessionRequest,
    GateTestnetMarketSessionService,
)
from app.services.gate_testnet_execution_rehearsal_service import GateTestnetExecutionRehearsalService
from app.services.gate_testnet_execution_worker import GateTestnetExecutionWorker
from app.services.gate_testnet_order_client import GateTestnetOrderReceipt
from app.services.immutable_fill_ledger_repository import (
    FillLedgerCommitDisposition,
    FillLedgerCommitResult,
    FillLedgerPersistenceScope,
)
from app.services.submission_attempt_repository import (
    SubmissionAttemptCreateFacts,
    SubmissionAttemptDisposition,
    SubmissionAttemptPersistenceResult,
    SubmissionAttemptRepository,
)
from app.services.exchange_order_repository import ExchangeOrderRepository


UTC = timezone.utc
NON_LIVE_PRODUCT_REHEARSAL_VERSION = "non-live-product-rehearsal-v1"
_OBSERVED_AT = datetime(2026, 1, 1, 0, 8, tzinfo=UTC)


class NonLiveProductRehearsalError(ValueError):
    """Fixture evidence could not form a safe complete product rehearsal."""


class _FixtureSubmissionAttemptRepository(SubmissionAttemptRepository):
    """No-DB attempt arbiter for the deterministic offline rehearsal only."""

    def persist_caller_owned(self, connection: object, facts: SubmissionAttemptCreateFacts) -> SubmissionAttemptPersistenceResult:
        return SubmissionAttemptPersistenceResult(facts.id, SubmissionAttemptDisposition.APPLIED)


class _FixtureExchangeOrderRepository(ExchangeOrderRepository):
    """No-DB exchange-order arbiter for the deterministic offline rehearsal."""

    def persist_caller_owned(self, connection: object, facts):
        return facts.id, "APPLIED"


class _FixtureOrderStateRepository:
    """No-DB state-event sink for the deterministic offline rehearsal only."""

    def apply_attempt_transition_caller_owned(self, connection: object, transition: object) -> None:
        # The offline rehearsal exercises transition authorization and worker
        # ordering, but deliberately does not create durable state events.
        return None


class _FixtureDurableEntryPort:
    """Deterministic receipt port used only by the offline rehearsal.

    The real persistence adapters remain the authority for production.  This
    port deliberately has no connection, cursor, commit, rollback, or
    database access; it lets the product smoke surface exercise the same
    gateway contracts without creating durable facts.
    """

    def persist_durable_entry(self, _connection: object, graph: Any) -> DurableEntryPersistResult:
        subject = graph.subject
        economic_order_id = getattr(subject, "economic_order_id", None)
        return DurableEntryPersistResult(
            graph.command_id,
            graph.specification.action,
            subject,
            economic_order_id,
            graph.specification.economic_fingerprint,
            graph.specification.request_fingerprint,
            DurableEntryPersistDisposition.CREATED,
        )


class _FixtureDurableRiskPort:
    """Typed ALLOW/Reservation receipt for the deterministic OPEN fixture."""

    def evaluate_and_persist(self, _connection: object, graph: Any) -> DurableRiskPersistResultV2:
        specification = graph.specification
        subject = graph.subject
        decision_id = str(uuid5(NAMESPACE_URL, f"product-rehearsal:risk:{graph.command_id}"))
        reservation_id = str(uuid5(NAMESPACE_URL, f"product-rehearsal:reservation:{graph.command_id}"))
        return DurableRiskPersistResultV2(
            command_id=graph.command_id,
            economic_order_id=subject.economic_order_id,
            durable_entry_contract_version=DURABLE_ENTRY_CONTRACT_VERSION,
            economic_fingerprint=specification.economic_fingerprint,
            request_fingerprint=specification.request_fingerprint,
            tenant_id=specification.tenant_id,
            credential_id=specification.credential_id,
            account_scope=specification.account_scope,
            instrument_id=specification.instrument_id,
            market_type=specification.market_type,
            action=specification.action,
            risk_effect=specification.risk_effect,
            actor_type=specification.actor.actor_type.value,
            actor_id=specification.actor.actor_id,
            source=specification.actor.entry_source.value,
            mode=specification.mode.value,
            correlation_id=specification.correlation_id,
            entry_occurred_at=specification.occurred_at,
            scope_fingerprint="a" * 64,
            audit_fingerprint="b" * 64,
            decision_id=decision_id,
            reservation_id=reservation_id,
            allowed=True,
            decision_status="ALLOW",
            decision_fingerprint="c" * 64,
            disposition=DurableRiskPersistDisposition.CREATED,
        )


class _FixtureAdmissionOutboxPort:
    """Build the shared deterministic event without persisting it."""

    def persist_admission(
        self,
        _connection: object,
        graph: Any,
        _durable_result: DurableEntryPersistResult,
        risk_result: DurableRiskPersistResultV2,
    ) -> OutboxPersistResult:
        event = deterministic_admission_outbox_event(graph, risk_result=risk_result)
        return OutboxPersistResult(event, OutboxPersistDisposition.CREATED)


def _fixture_admit(graph: Any):
    """Run the real gateway once and return its typed receipt for composition."""

    gateway = CanonicalEntryAdmissionGateway(
        durable_entries=_FixtureDurableEntryPort(),
        durable_risk=_FixtureDurableRiskPort(),
        outbox=_FixtureAdmissionOutboxPort(),
    )
    return gateway.admit(object(), graph)


def _fixture_transport(request: Any) -> GateReadResponse:
    if request.endpoint is GatePublicReadEndpoint.CANDLESTICKS:
        return GateReadResponse(200, [
            [1767225600, "200", "101", "102", "99", "100", "2", True],
            [1767225660, "202", "102", "103", "99", "101", "2", True],
            [1767225720, "204", "103", "104", "100", "102", "2", True],
            [1767225780, "206", "104", "105", "101", "103", "2", True],
            [1767225840, "208", "105", "106", "102", "104", "2", True],
            [1767225900, "220", "115", "116", "100", "105", "2", True],
            [1767225960, "222", "116", "117", "113", "115", "2", True],
            [1767226020, "224", "117", "118", "114", "116", "2", True],
        ])
    return GateReadResponse(200, {
        "id": 7,
        "current": 1767225900000,
        "update": 1767225899000,
        "bids": [["114", "1"]],
        "asks": [["115", "2"]],
    })


def _paper_snapshot(result: Any) -> ReadonlyPaperAccountSnapshot:
    intents = {item.order_id: item for item in result.deterministic_backtest.orders}
    orders = []
    for decision in result.deterministic_backtest.trace.decisions:
        if decision.decision.value != "executed":
            continue
        intent = intents[decision.order_id]
        orders.append(ReadonlyPaperOrderFact(
            order_uid=decision.order_id,
            market="paper",
            symbol=intent.instrument_id,
            side=intent.side.value,
            order_type=intent.execution_kind.value,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            fill_price=decision.fill_price,
            fill_value=intent.quantity * (decision.fill_price or Decimal("0")),
            status=PaperOrderStatus.FILLED,
            note="offline-product-rehearsal",
            created_at=decision.fill_time or intent.submitted_at,
        ))
    return ReadonlyPaperAccountSnapshot(1, tuple(orders), _OBSERVED_AT)


def _candidate_graph(result: Any) -> Any | None:
    """Convert the first non-flat strategy fact into an immutable V2 graph.

    The conversion is intentionally in-memory and read-only.  It proves that
    the research signal has a lossless Candidate -> Canonical Entry hand-off,
    without opening a connection or invoking Admission persistence.
    """

    signal = next((item for item in result.deterministic_backtest.signals if item.direction is not SignalDirection.FLAT), None)
    if signal is None:
        return None
    strategy_id = int(strategy_fingerprint(signal.strategy)[:12], 16) % 2_000_000_000 or 1
    run_id = int(strategy_fingerprint(result.deterministic_backtest.run)[:12], 16) % 2_000_000_000 or 1
    try:
        candidate = candidate_from_strategy_signal(
            signal,
            strategy_id=strategy_id,
            strategy_run_id=run_id,
            action=OrderAction.OPEN,
            execution_kind=ExecutionKind.MARKET,
            quantity=Decimal("1"),
            market_type="spot",
        )
        command_id = str(uuid5(NAMESPACE_URL, f"product-rehearsal:command:{signal.signal_id}"))
        economic_order_id = str(uuid5(NAMESPACE_URL, f"product-rehearsal:economic-order:{signal.signal_id}"))
        graph = candidate.to_graph(
            command_id=command_id,
            economic_order_id=economic_order_id,
            tenant_id=1,
            credential_id=1,
            account_scope="paper",
            correlation_id="product-rehearsal",
            occurred_at=signal.occurred_at,
            mode=EntryMode.PAPER,
        )
        return graph
    except Exception as exc:
        raise NonLiveProductRehearsalError("strategy signal cannot become a canonical candidate") from exc


def _candidate_entry(result: Any) -> dict[str, object] | None:
    """Expose the Candidate -> Canonical graph as JSON-safe evidence."""

    graph = _candidate_graph(result)
    if graph is None:
        return None
    try:
        signal = next(item for item in result.deterministic_backtest.signals if item.direction is not SignalDirection.FLAT)
        strategy_id = int(strategy_fingerprint(signal.strategy)[:12], 16) % 2_000_000_000 or 1
        run_id = int(strategy_fingerprint(result.deterministic_backtest.run)[:12], 16) % 2_000_000_000 or 1
        return {
            "candidate_version": "strategy-v2-candidate-v1",
            "strategy_id": strategy_id,
            "strategy_run_id": run_id,
            "signal_id": signal.signal_id,
            "command_id": graph.command_id,
            "economic_order_id": graph.subject.economic_order_id,
            "action": graph.specification.action.value,
            "execution_kind": graph.specification.economic_intent.execution_kind.value,
            "quantity": graph.specification.economic_intent.quantity.to_string(),
            "mode": graph.specification.mode.value,
            "economic_fingerprint": graph.specification.economic_fingerprint,
            "request_fingerprint": graph.specification.request_fingerprint,
            "admission_persistence": "NOT_PERSISTED_OFFLINE_REHEARSAL",
        }
    except Exception as exc:
        raise NonLiveProductRehearsalError("canonical candidate evidence cannot be formatted") from exc


def _admission_rehearsal(result: Any) -> dict[str, object] | None:
    """Run the real Admission Gateway against typed, non-persisting ports."""

    graph = _candidate_graph(result)
    if graph is None:
        return None
    try:
        receipt = _fixture_admit(graph)
        event = deterministic_admission_outbox_event(
            graph,
            risk_result=_FixtureDurableRiskPort().evaluate_and_persist(object(), graph),
        )
        parsed = parse_admission_outbox_event(event)
        return {
            "disposition": receipt.disposition.value,
            "risk_decision_status": receipt.risk_decision_status,
            "reservation_id": receipt.reservation_id,
            "outbox_event_id": receipt.outbox_event_id,
            "outbox_payload_hash": receipt.outbox_payload_hash,
            "typed_event_parser": "PASS" if parsed.command_id == graph.command_id else "FAIL",
            "persistence": "NOT_INVOKED_READ_ONLY_REHEARSAL",
            "transaction_owner": "CALLER_NOT_GATEWAY",
        }
    except Exception as exc:
        raise NonLiveProductRehearsalError("offline admission rehearsal failed closed") from exc


class _FixtureTestnetExecutionClient:
    """Translate the deterministic execution fixture to the order boundary."""

    def submit(self, request: GateTestnetExecutionRequest) -> GateTestnetOrderReceipt:
        simulated = simulate_gate_testnet_execution(request)
        # The full test suite deliberately loads some immutable contracts in
        # isolated module namespaces.  Resolve the exact classes used by the
        # injected execution worker so the fixture remains typed without
        # weakening the worker's isinstance boundary.
        worker_module = importlib.import_module(GateTestnetExecutionWorker.__module__)
        receipt_type = worker_module.GateTestnetOrderReceipt
        # A full unittest discovery can retain a class whose ``__module__``
        # name points at a restored module object.  Resolve every related
        # contract from the class globals instead of importing by name, so
        # isinstance checks remain valid across those isolated namespaces.
        receipt_globals = receipt_type.__post_init__.__globals__
        receipt_market_type_type = receipt_globals["AssetMarketType"]
        receipt_side_type = receipt_globals["GateOrderSide"]
        receipt_fill_type = receipt_globals["GateFillFact"]
        execution_receipt_type = receipt_globals["GateTestnetExecutionReceipt"]
        execution_globals = execution_receipt_type.__post_init__.__globals__
        execution_request_type = execution_globals["GateTestnetExecutionRequest"]
        execution_market_type_type = execution_globals["AssetMarketType"]
        execution_side_type = execution_globals["GateOrderSide"]
        execution_kind_type = execution_globals["GateExecutionKind"]
        execution_environment_type = execution_globals["CapabilityEnvironment"]
        execution_fill_type = execution_globals["GateFillFact"]
        execution_order_type = execution_globals["GateOrderFact"]
        execution_status_type = execution_globals["GateOrderStatus"]
        execution_disposition_type = execution_globals["GateExecutionDisposition"]
        receipt_market_type = receipt_market_type_type(request.market_type.value)
        receipt_side = receipt_side_type(request.side.value)
        execution_market_type = execution_market_type_type(request.market_type.value)
        execution_side = execution_side_type(request.side.value)
        receipt_request = execution_request_type(
            instrument_id=request.instrument_id,
            market_type=execution_market_type,
            account_scope=request.account_scope,
            side=execution_side,
            quantity=request.quantity,
            reference_price=request.reference_price,
            execution_kind=execution_kind_type(request.execution_kind.value),
            limit_price=request.limit_price,
            fill_ratio=request.fill_ratio,
            fee_rate=request.fee_rate,
            fee_asset=request.fee_asset,
            client_order_id=request.client_order_id,
            reduce_only=request.reduce_only,
            observed_at=request.observed_at,
            environment=execution_environment_type(request.environment.value),
        )
        receipt_fills = tuple(
            receipt_fill_type(
                venue_id=fill.venue_id,
                market_type=receipt_market_type,
                account_scope=fill.account_scope,
                instrument_id=fill.instrument_id,
                exchange_order_id=fill.exchange_order_id,
                venue_fill_id=fill.venue_fill_id,
                side=receipt_side,
                quantity=fill.quantity,
                price=fill.price,
                fee_asset=fill.fee_asset,
                fee_amount=fill.fee_amount,
                observed_at=fill.observed_at,
                source_event_id=fill.source_event_id,
            )
            for fill in simulated.fills
        )
        execution_fills = tuple(
            execution_fill_type(
                venue_id=fill.venue_id,
                market_type=execution_market_type,
                account_scope=fill.account_scope,
                instrument_id=fill.instrument_id,
                exchange_order_id=fill.exchange_order_id,
                venue_fill_id=fill.venue_fill_id,
                side=execution_side,
                quantity=fill.quantity,
                price=fill.price,
                fee_asset=fill.fee_asset,
                fee_amount=fill.fee_amount,
                observed_at=fill.observed_at,
                source_event_id=fill.source_event_id,
            )
            for fill in simulated.fills
        )
        receipt_order = execution_order_type(
            venue_id=simulated.order.venue_id,
            market_type=execution_market_type,
            account_scope=simulated.order.account_scope,
            instrument_id=simulated.order.instrument_id,
            exchange_order_id=simulated.order.exchange_order_id,
            client_order_id=simulated.order.client_order_id,
            side=execution_side,
            status=execution_status_type(simulated.order.status.value),
            quantity=simulated.order.quantity,
            filled_quantity=simulated.order.filled_quantity,
            average_fill_price=simulated.order.average_fill_price,
            observed_at=simulated.order.observed_at,
            source_event_id=simulated.order.source_event_id,
            raw_status=simulated.order.raw_status,
            finish_reason=simulated.order.finish_reason,
        )
        receipt_lifecycle = execution_receipt_type(
            receipt_request,
            execution_disposition_type(simulated.disposition.value),
            receipt_order,
            execution_fills,
            simulated.fee_amount,
        )
        return receipt_type(
            market_type=receipt_market_type,
            account_scope=request.account_scope,
            instrument_id=request.instrument_id,
            client_order_id=request.client_order_id,
            exchange_order_id=simulated.order.exchange_order_id,
            raw_state=simulated.order.raw_status,
            status_code=200,
            response_fingerprint=simulated.lifecycle_fingerprint,
            fills=receipt_fills,
            fee_amount=simulated.fee_amount,
            execution_receipt=receipt_lifecycle,
        )


class _FixtureLedgerRepository:
    """Caller-owned in-memory ledger receipt for the offline composition."""

    def __init__(self) -> None:
        self.fills: list[str] = []

    def persist_fill_bundle_caller_owned(self, _connection, *, scope, fill):
        self.fills.append(fill.fill_key)
        return FillLedgerCommitResult(
            fill_event_id=str(uuid5(NAMESPACE_URL, f"fixture:fill:{fill.fill_key}")),
            trade_transaction_id=str(uuid5(NAMESPACE_URL, f"fixture:trade:{fill.fill_key}")),
            fee_transaction_id=str(uuid5(NAMESPACE_URL, f"fixture:fee:{fill.fill_key}")) if fill.fee_components else None,
            replay_fingerprint=fill.fill_key,
            disposition=FillLedgerCommitDisposition.APPLIED,
        )


def _execution_worker_rehearsal(result: Any) -> dict[str, object] | None:
    """Exercise admission -> TestNet boundary -> ledger composition offline."""

    graph = _candidate_graph(result)
    if graph is None:
        return None
    try:
        admission = _fixture_admit(graph)
        intent = graph.specification.economic_intent
        if intent.side is None or intent.quantity is None:
            raise NonLiveProductRehearsalError("fixture graph has no executable quantity")
        request = GateTestnetExecutionRequest(
            instrument_id=graph.specification.instrument_id,
            # Use the enum objects imported by the request contract itself.
            # This keeps the typed boundary stable when an offline fixture
            # loader has isolated the shared domain modules.
            market_type=GateExecutionAssetMarketType(graph.specification.market_type),
            account_scope=graph.specification.account_scope,
            side=GateExecutionOrderSide(intent.side.value.lower()),
            quantity=intent.quantity.to_decimal(),
            reference_price=Decimal("100"),
            execution_kind=GateExecutionKind(intent.execution_kind.value.lower()),
            trigger_price=None if intent.trigger_price is None else intent.trigger_price.to_decimal(),
            trigger_direction=None if intent.trigger_direction is None else GateTriggerDirection(intent.trigger_direction.value),
            trigger_price_type=None if intent.trigger_price_type is None else GateTriggerPriceType(intent.trigger_price_type.value),
            client_order_id="fixture-admission-order",
            observed_at=graph.specification.occurred_at,
        )
        repository = _FixtureLedgerRepository()
        worker_globals = GateTestnetExecutionWorker.execute.__globals__
        ledger_scope_type = worker_globals["GateTestnetLedgerScope"]
        persistence_scope_type = worker_globals["FillLedgerPersistenceScope"]
        ledger_scope_globals = ledger_scope_type.__post_init__.__globals__
        instrument_asset_scope_type = ledger_scope_globals["InstrumentAssetScope"]
        persistence_scope = persistence_scope_type(
            tenant_id=graph.specification.tenant_id,
            credential_id=graph.specification.credential_id,
            intent_id=str(uuid5(NAMESPACE_URL, f"fixture:intent:{graph.command_id}")),
            economic_order_id=graph.subject.economic_order_id,
            source="MANUAL",
            exchange_event_at=graph.specification.occurred_at,
            received_at=graph.specification.occurred_at,
            normalizer_version="fixture-v1",
            instrument_rule_version="fixture-rule-v1",
        )
        attempt_facts = SubmissionAttemptCreateFacts(
            id=str(uuid5(NAMESPACE_URL, f"fixture:attempt:{graph.command_id}")),
            scope=SubmissionAttemptScope(
                graph.specification.tenant_id,
                graph.specification.credential_id,
                graph.specification.account_scope,
                graph.specification.instrument_id,
                graph.specification.market_type,
                graph.subject.economic_order_id,
                "gate",
            ),
            child_seq=1,
            attempt_no=1,
            role="PRIMARY",
            canonical_client_order_id="fixture-admission-order",
            venue_client_order_id="t-fixture-admission-order",
            request_fingerprint=graph.specification.request_fingerprint,
            request_json_redacted={
                "source": "offline-fixture",
                "client_order_id": "fixture-admission-order",
                "quantity": intent.quantity.to_string(),
            },
            venue_capability_snapshot_id=str(uuid5(NAMESPACE_URL, f"fixture:capability:{graph.command_id}")),
            recovery_policy_snapshot_id=str(uuid5(NAMESPACE_URL, f"fixture:policy:{graph.command_id}")),
            client_id_algorithm_version="gate-client-v1",
            broker_prefix_normalization_version="prefix-v1",
            broker_prefix="fixture",
        )
        execution = GateTestnetExecutionWorker(_FixtureTestnetExecutionClient(), enabled=True).execute(
            object(), graph, admission, request,
            ledger_scope=ledger_scope_type(
                economic_order_id=graph.subject.economic_order_id,
                assets=instrument_asset_scope_type("BTC_USDT", "BTC", "USDT"),
                valuation_ccy="USDT",
            ),
            persistence_scope=persistence_scope,
            ledger_repository=repository,
            attempt_facts=attempt_facts,
            attempt_repository=_FixtureSubmissionAttemptRepository(),
            exchange_order_repository=_FixtureExchangeOrderRepository(),
            state_repository=_FixtureOrderStateRepository(),
        )
        return {
            "status": "READY",
            "admission_disposition": admission.disposition.value,
            "order_id": execution.receipt.exchange_order_id,
            "fill_count": len(execution.receipt.fills),
            "ledger_disposition": None if execution.ledger is None else execution.ledger.disposition,
            "persisted_fill_keys": list(repository.fills),
            "network_access": False,
            "writes_enabled": False,
            "live_enabled": execution.live_enabled,
        }
    except Exception as exc:
        raise NonLiveProductRehearsalError("offline execution boundary rehearsal failed closed") from exc


def _ledger_rehearsal(receipt: Any, graph: Any | None) -> dict[str, object]:
    """Show the exact fill-ledger hand-off without persisting a fact."""

    economic_order_id = graph.subject.economic_order_id if graph is not None else str(
        uuid5(NAMESPACE_URL, "product-rehearsal:economic-order:fixture")
    )
    try:
        ledger_builder = build_gate_testnet_ledger_inputs
        builder_globals = ledger_builder.__globals__
        fill_builder = builder_globals["build_gate_fill_ledger_input"]
        fill_builder_globals = fill_builder.__globals__
        fill_type = fill_builder_globals["GateFillFact"]
        fill_globals = fill_type.__post_init__.__globals__
        fill_market_type = fill_globals["AssetMarketType"]
        fill_side_type = fill_globals["GateOrderSide"]
        scope_type = builder_globals["GateTestnetLedgerScope"]
        scope_globals = scope_type.__post_init__.__globals__
        asset_scope_type = scope_globals["InstrumentAssetScope"]
        ledger_scope = scope_type(
            economic_order_id=economic_order_id,
            assets=asset_scope_type("BTC_USDT", "BTC", "USDT"),
            valuation_ccy="USDT",
        )
        inputs = tuple(
            fill_builder(
                fill_type(
                    venue_id=item.venue_id,
                    market_type=fill_market_type(item.market_type.value),
                    account_scope=item.account_scope,
                    instrument_id=item.instrument_id,
                    exchange_order_id=item.exchange_order_id,
                    venue_fill_id=item.venue_fill_id,
                    side=fill_side_type(item.side.value),
                    quantity=item.quantity,
                    price=item.price,
                    fee_asset=item.fee_asset,
                    fee_amount=item.fee_amount,
                    observed_at=item.observed_at,
                    source_event_id=item.source_event_id,
                ),
                economic_order_id=economic_order_id,
                scope=ledger_scope,
            )
            for item in receipt.fills
        )
    except Exception as exc:
        raise NonLiveProductRehearsalError("TestNet fill cannot form an immutable ledger input") from exc
    return {
        "status": "READY",
        "persistence": "NOT_PERSISTED_OFFLINE_REHEARSAL",
        "transaction_boundary": "IMMUTABLE_FILL_LEDGER_BUNDLE",
        "fill_count": len(inputs),
        "fills": [
            {
                "fill_key": item.fill_key,
                "economic_order_id": item.economic_order_id,
                "quote_quantity": item.quote_quantity.amount.to_string(),
                "quote_policy_version": item.quote_quantity.calculation_policy_version,
                "fee_assets": [component.fee.asset for component in item.fee_components],
                "trade_replay_fingerprint": item.fill_key,
            }
            for item in inputs
        ],
    }


def build_offline_product_rehearsal() -> dict[str, object]:
    """Run one complete deterministic fixture rehearsal and return JSON-safe facts."""

    profile = GateReadCapabilityProfile(GateEnvironment.TESTNET, GateMarketType.SPOT, credential_ref="offline-fixture")
    adapter = GateReadonlyAdapter(profile, _fixture_transport)
    session_service = GateTestnetMarketSessionService(
        GateMarketResearchService(adapter, "fixture", "fixture-evidence")
    )
    request = GateTestnetMarketSessionRequest(
        "BTC_USDT", _OBSERVED_AT, "product-smoke-dataset", "gate-rules-v1"
    )
    strategy = StrategyDefinition(
        "ict-liquidity-displacement", "ict-v1", StrategyFamily.ICT, "ict-schema-v1", "gate-ohlcv-pit-v1",
        (StrategyParameterFact("lookback", "3"), StrategyParameterFact("multiplier", "1.5")),
        # The fixture session intentionally uses the canonical 1-minute Gate
        # candles.  Keep the rehearsal strategy scope aligned with the
        # observed dataset so the same runtime scope contract is exercised by
        # the offline product smoke path.
        ("1m", "5m", "15m", "1h"),
        ("crypto",),
    )
    sizing = PositionSizingRequest(
        "product-smoke-sizing", "BTC_USDT", Decimal("100"), Decimal("1"), Decimal("1000"),
        Decimal("20000"), Decimal("2"), Decimal("0.5"), _OBSERVED_AT,
    )
    paper_run = PaperShadowRunFacts(
        "product-smoke-paper", SimulationMode.PAPER, "product-smoke-dataset", strategy.strategy_id,
        "product-smoke-risk", "product-smoke-tolerance", _OBSERVED_AT,
    )
    cost_policy = BacktestCostPolicySnapshot(
        "product-smoke-cost-v1", "USDT", Decimal("0.0002"), Decimal("0.0005"),
        Decimal("2"), Decimal("3"), Decimal("0.0001"), 28800,
        "product-smoke-cost-evidence",
    )
    backtest_run = BacktestRunFacts(
        "product-smoke-backtest", "product-smoke-dataset", "gate-rules-v1", "fee-v1", "slippage-v1",
        Decimal("10000"), "USDT", datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 9, tzinfo=UTC), cost_policy_fingerprint(cost_policy),
    )
    try:
        result = GateNonLiveResearchOrchestrator(session_service).run(
            request, strategy, sizing, paper_run, backtest_run,
            signal_id="product-smoke-signal", request_fingerprint=sizing.request_fingerprint,
            decided_at=_OBSERVED_AT, order_quantity=Decimal("1"), execution_kind=BacktestExecutionKind.MARKET,
            cost_policy=cost_policy,
        )
        paper = _paper_snapshot(result)
        paper_recovery = verify_paper_snapshot_recovery(
            paper,
            expected_snapshot_fingerprint=paper.snapshot_fingerprint,
        )
        candidate_entry = _candidate_entry(result)
        admission = _admission_rehearsal(result)
        execution_worker = _execution_worker_rehearsal(result)
        testnet_execution = GateTestnetExecutionRehearsalService().run(
            instrument_id=result.dataset.instrument_id,
            market_type="perpetual",
            fill_ratio="0.5",
        )
        ledger_rehearsal = _ledger_rehearsal(testnet_execution, _candidate_graph(result))
        canary_evidence = CanaryReleaseEvidence(
            release_id="product-rehearsal-release",
            artifact_digest="d" * 64,
            sample_count=1,
            error_count=0,
            shadow_match_rate=Decimal("1"),
            reconciliation_healthy=True,
            kill_switch_clear=True,
            rollback_verified=True,
            observed_at=_OBSERVED_AT,
        )
        canary_gate = evaluate_canary_promotion(canary_evidence)
        return {
            "contract_version": NON_LIVE_PRODUCT_REHEARSAL_VERSION,
            "environment": {"PAPER": True, "SHADOW": True, "TESTNET": True, "CANARY": False, "LIVE": False},
            "market": {
                "venue": "gate",
                "instrument_id": result.dataset.instrument_id,
                "dataset_fingerprint": result.dataset.dataset_fingerprint,
                "bar_count": len(result.dataset.bars),
            },
            "strategy_catalog": [
                {
                    "strategy_id": item.strategy_id,
                    "version": item.version,
                    "family": item.family.value,
                    "supported_timeframes": list(item.supported_timeframes),
                    "supported_market_types": list(item.supported_market_types),
                }
                for item in builtin_strategy_catalog()
            ],
            "research": result.to_public_dict(),
            "candidate_entry": candidate_entry,
            "admission": admission,
            "execution_worker": execution_worker,
            "deterministic_backtest": result.deterministic_backtest.to_public_dict(),
            "paper_account": paper.to_public_dict(),
            "paper_recovery": paper_recovery.to_public_dict(),
            "testnet_execution": testnet_execution.to_public_dict(),
            "ledger_rehearsal": ledger_rehearsal,
            "canary_gate": {
                "decision": canary_gate.decision.value,
                "reasons": list(canary_gate.reasons),
                "evidence_fingerprint": canary_gate.evidence_fingerprint,
                "live_enabled": False,
            },
            "execution_boundary": "READ_ONLY_FIXTURE",
            "network_access": False,
            "live_enabled": False,
        }
    except Exception as exc:
        raise NonLiveProductRehearsalError("non-live product rehearsal failed closed") from exc


__all__ = [
    "NON_LIVE_PRODUCT_REHEARSAL_VERSION",
    "NonLiveProductRehearsalError",
    "build_offline_product_rehearsal",
]
