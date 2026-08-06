"""Guarded Gate TestNet order composition.

The service is the only HTTP-facing bridge from a Canonical Entry admission to
the opt-in TestNet order worker.  It keeps admission, order submission and any
future fill settlement on a caller-owned connection.  LIVE is never accepted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping

from app.domain.canonical_entry_v2_contracts import CancelTargetKind, DurableEntryGraphV2
from app.domain.canonical_entry_contracts import ExecutionKind
from app.domain.entry_admission_v2_contracts import EntryAdmissionDisposition
from app.domain.gate_testnet_execution_contracts import (
    GateExecutionKind,
    GateOrderSide,
    GateTestnetExecutionRequest,
    GateTriggerDirection,
    GateTriggerPriceType,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.domain.decimal_values import Price
from app.domain.immutable_fill_ledger import InstrumentAssetScope
from app.domain.order_contracts import OrderAction
from app.services.gate_testnet_execution_worker import GateTestnetExecutionResult, GateTestnetExecutionWorker
from app.services.immutable_fill_ledger_repository import FillLedgerPersistenceScope
from app.domain.gate_testnet_ledger_contracts import GateTestnetLedgerScope
from app.services.runtime_entry_admission_http_service import admit_runtime_entry_payload_caller_owned
from app.services.submission_attempt_repository import SubmissionAttemptCreateFacts, SubmissionAttemptRepository
from app.services.exchange_order_repository import ExchangeOrderRepository


class GateTestnetExecutionServiceError(RuntimeError):
    """The TestNet execution request is invalid or not authorized."""


_GATE_CLIENT_ORDER_ID = re.compile(r"^[A-Za-z0-9._-]{1,28}$")


@dataclass(frozen=True, slots=True)
class GateTestnetCancelRequest:
    """Typed cancel facts accepted by the explicit TestNet cancel seam."""

    instrument_id: str
    market_type: AssetMarketType
    account_scope: str
    exchange_order_id: str

    def __post_init__(self) -> None:
        for value, field_name in ((self.instrument_id, "instrument_id"), (self.account_scope, "account_scope"), (self.exchange_order_id, "exchange_order_id")):
            if not isinstance(value, str) or not value or value != value.strip() or not value.isascii() or any(char.isspace() for char in value):
                raise GateTestnetExecutionServiceError(f"{field_name} must be canonical ASCII text")
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateTestnetExecutionServiceError("market_type must be spot or perpetual")


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, (bool, float)):
        raise GateTestnetExecutionServiceError(f"{field_name} must use Decimal-compatible text")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateTestnetExecutionServiceError(f"{field_name} must be Decimal-compatible") from exc
    if not result.is_finite() or result <= 0:
        raise GateTestnetExecutionServiceError(f"{field_name} must be positive and finite")
    return result


def _utc(value: object) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GateTestnetExecutionServiceError("observed_at must be an ISO timestamp") from exc
    else:
        raise GateTestnetExecutionServiceError("observed_at is required")
    if result.tzinfo is None or result.utcoffset() != timezone.utc.utcoffset(result):
        raise GateTestnetExecutionServiceError("observed_at must use UTC")
    return result.astimezone(timezone.utc)


def _market(value: str) -> AssetMarketType:
    normalized = str(value or "").strip().lower()
    if normalized == "spot":
        return AssetMarketType.SPOT
    if normalized in {"perpetual", "perp", "swap", "futures", "future"}:
        return AssetMarketType.PERPETUAL
    raise GateTestnetExecutionServiceError("market_type must be spot or perpetual")


def _execution(value: ExecutionKind) -> GateExecutionKind:
    if value is ExecutionKind.MARKET:
        return GateExecutionKind.MARKET
    if value is ExecutionKind.LIMIT:
        return GateExecutionKind.LIMIT
    if value is ExecutionKind.STOP_MARKET:
        return GateExecutionKind.STOP_MARKET
    if value is ExecutionKind.STOP_LIMIT:
        return GateExecutionKind.STOP_LIMIT
    raise GateTestnetExecutionServiceError("unsupported execution kind")


def _trigger_direction(value: object) -> GateTriggerDirection:
    try:
        return GateTriggerDirection(str(getattr(value, "value", value)))
    except ValueError as exc:
        raise GateTestnetExecutionServiceError("trigger_direction is unsupported") from exc


def _trigger_price_type(value: object) -> GateTriggerPriceType:
    try:
        return GateTriggerPriceType(str(getattr(value, "value", value)))
    except ValueError as exc:
        raise GateTestnetExecutionServiceError("trigger_price_type is unsupported") from exc


def _client_order_id(payload: Mapping[str, Any], graph: DurableEntryGraphV2) -> str:
    supplied = payload.get("client_order_id")
    value = str(supplied) if supplied is not None else f"gate-v1-{graph.specification.economic_fingerprint[:20]}"
    if value != value.strip() or not value.isascii():
        raise GateTestnetExecutionServiceError("client_order_id is invalid for Gate TestNet")
    content = value[2:] if value.startswith("t-") else value
    if not _GATE_CLIENT_ORDER_ID.fullmatch(content):
        raise GateTestnetExecutionServiceError("client_order_id is invalid for Gate TestNet")
    return f"t-{content}"


def build_gate_testnet_execution_request(payload: Mapping[str, Any], graph: DurableEntryGraphV2) -> GateTestnetExecutionRequest:
    """Build a venue request from the already validated canonical graph."""

    if not isinstance(payload, Mapping) or not isinstance(graph, DurableEntryGraphV2):
        raise GateTestnetExecutionServiceError("typed payload and canonical graph are required")
    specification = graph.specification
    if specification.action is OrderAction.CANCEL:
        raise GateTestnetExecutionServiceError("CANCEL requires the cancel/query boundary")
    intent = specification.economic_intent
    if intent.side is None or intent.execution_kind is None:
        raise GateTestnetExecutionServiceError("executable side and execution kind are required")
    quantity = intent.quantity or intent.close_quantity
    if quantity is None or intent.close_all:
        raise GateTestnetExecutionServiceError("TestNet execution requires an explicit quantity")
    reference_price = _decimal(payload.get("reference_price"), "reference_price")
    limit_price = None if intent.limit_price is None else _decimal(intent.limit_price.value, "limit_price")
    if intent.execution_kind in (ExecutionKind.LIMIT, ExecutionKind.STOP_LIMIT) and limit_price is None:
        raise GateTestnetExecutionServiceError("LIMIT/STOP_LIMIT requires limit_price")
    trigger_price = None if intent.trigger_price is None else _decimal(intent.trigger_price.value, "trigger_price")
    trigger_direction = None if intent.trigger_direction is None else _trigger_direction(intent.trigger_direction)
    trigger_price_type = None if intent.trigger_price_type is None else _trigger_price_type(intent.trigger_price_type)
    return GateTestnetExecutionRequest(
        instrument_id=specification.instrument_id,
        market_type=_market(specification.market_type),
        account_scope=specification.account_scope,
        side=GateOrderSide(str(intent.side.value).lower()),
        quantity=_decimal(quantity.value, "quantity"),
        reference_price=reference_price,
        execution_kind=_execution(intent.execution_kind),
        limit_price=limit_price,
        trigger_price=trigger_price,
        trigger_direction=trigger_direction,
        trigger_price_type=trigger_price_type,
        client_order_id=_client_order_id(payload, graph),
        reduce_only=bool(intent.reduce_only),
        observed_at=_utc(payload.get("observed_at") or specification.occurred_at),
        environment=CapabilityEnvironment.TESTNET,
    )


def build_gate_testnet_execution_ledger_scopes(
    payload: Mapping[str, Any],
    graph: DurableEntryGraphV2,
    *,
    tenant_id: int,
    credential_id: int,
) -> tuple[GateTestnetLedgerScope, FillLedgerPersistenceScope]:
    """Build explicit Canonical Entry V2 fill scopes before TestNet submission.

    The durable command/economic identity comes only from the already persisted
    graph.  Asset and valuation facts remain caller-provided and must be
    complete; no symbol parsing, exchange lookup, current time, or guessed FX
    rate is allowed at this boundary.
    """

    if not isinstance(payload, Mapping) or not isinstance(graph, DurableEntryGraphV2):
        raise GateTestnetExecutionServiceError("typed payload and canonical graph are required")
    if graph.specification.action is OrderAction.CANCEL:
        raise GateTestnetExecutionServiceError("CANCEL cannot create a fill ledger scope")
    subject_id = getattr(graph.subject, "economic_order_id", None)
    if subject_id is None:
        raise GateTestnetExecutionServiceError("economic order subject is required")
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id < 1:
        raise GateTestnetExecutionServiceError("tenant_id must be positive")
    if isinstance(credential_id, bool) or not isinstance(credential_id, int) or credential_id < 1:
        raise GateTestnetExecutionServiceError("credential_id must be positive")
    payload_credential_id = payload.get("credential_id")
    if payload_credential_id != credential_id:
        raise GateTestnetExecutionServiceError("credential_id scope mismatch")

    def required_text(name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
            raise GateTestnetExecutionServiceError(f"{name} must be canonical ASCII text")
        return value

    base_asset = required_text("base_asset").upper()
    quote_asset = required_text("quote_asset").upper()
    valuation_ccy = required_text("valuation_ccy").upper()
    instrument_id = graph.specification.instrument_id
    try:
        assets = InstrumentAssetScope(instrument_id, base_asset, quote_asset)
        quote_price_raw = payload.get("quote_valuation_price")
        quote_price = None if quote_price_raw is None else Price(_decimal(quote_price_raw, "quote_valuation_price"))
        fee_prices_raw = payload.get("fee_valuation_prices") or {}
        if not isinstance(fee_prices_raw, Mapping):
            raise GateTestnetExecutionServiceError("fee_valuation_prices must be an object")
        fee_prices: dict[str, Price] = {}
        for raw_asset, raw_price in fee_prices_raw.items():
            if not isinstance(raw_asset, str) or not raw_asset or raw_asset != raw_asset.strip() or not raw_asset.isascii():
                raise GateTestnetExecutionServiceError("fee valuation asset is invalid")
            fee_prices[raw_asset.upper()] = Price(_decimal(raw_price, "fee_valuation_price"))
        observed_at = _utc(payload.get("observed_at") or graph.specification.occurred_at)
        exchange_event_at = _utc(payload.get("exchange_event_at"))
        received_at = _utc(payload.get("received_at"))
        ledger_scope = GateTestnetLedgerScope(
            economic_order_id=str(subject_id),
            assets=assets,
            valuation_ccy=valuation_ccy,
            quote_valuation_price=quote_price,
            fee_valuation_prices=fee_prices,
        )
        persistence_scope = FillLedgerPersistenceScope(
            tenant_id=tenant_id,
            credential_id=credential_id,
            intent_id=None,
            economic_order_id=str(subject_id),
            source=required_text("source").upper(),
            exchange_event_at=exchange_event_at,
            received_at=received_at,
            normalizer_version=required_text("normalizer_version"),
            instrument_rule_version=required_text("instrument_rule_version"),
            durable_entry_command_id=str(graph.command_id),
        )
    except GateTestnetExecutionServiceError:
        raise
    except Exception as exc:
        raise GateTestnetExecutionServiceError("TestNet ledger scope facts are invalid") from exc
    if persistence_scope.economic_order_id != ledger_scope.economic_order_id:
        raise GateTestnetExecutionServiceError("TestNet ledger scope identity mismatch")
    # Keep the observed timestamp validation explicit even though it is not
    # stored in FillLedgerPersistenceScope; it is the immutable request fact
    # used by the execution request itself.
    if observed_at.tzinfo is None:
        raise GateTestnetExecutionServiceError("observed_at must use UTC")
    return ledger_scope, persistence_scope


def execute_gate_testnet_payload_caller_owned(
    connection: object,
    payload: Mapping[str, Any],
    *,
    tenant_id: int,
    actor_id: str,
    client_factory: Callable[[GateTestnetExecutionRequest], Any],
    worker_factory: Callable[[Any], GateTestnetExecutionWorker] = lambda client: GateTestnetExecutionWorker(client, enabled=True),
    ledger_scope: GateTestnetLedgerScope | None = None,
    persistence_scope: FillLedgerPersistenceScope | None = None,
    ledger_repository: object | None = None,
    attempt_facts: SubmissionAttemptCreateFacts | None = None,
    attempt_repository: SubmissionAttemptRepository | None = None,
    exchange_order_repository: ExchangeOrderRepository | None = None,
) -> tuple[Any, GateTestnetExecutionResult]:
    """Admit and submit exactly once without committing or rolling back."""

    result, graph = admit_runtime_entry_payload_caller_owned(connection, payload, tenant_id=tenant_id, actor_id=actor_id)
    if result.admission is None or graph is None:
        raise GateTestnetExecutionServiceError("TestNet execution requires a persisted admission")
    if result.admission.disposition is not EntryAdmissionDisposition.CREATED:
        raise GateTestnetExecutionServiceError("replayed or rejected admission cannot submit a new TestNet order")
    # Preflight immutable ledger scopes before creating a venue client. A
    # filled response must never become an external fact that cannot be
    # recorded atomically by the caller-owned transaction.
    if (ledger_scope is None) != (persistence_scope is None):
        raise GateTestnetExecutionServiceError(
            "ledger_scope and persistence_scope must be supplied together"
        )
    if ledger_scope is None and persistence_scope is None:
        try:
            ledger_scope, persistence_scope = build_gate_testnet_execution_ledger_scopes(
                payload, graph, tenant_id=tenant_id, credential_id=payload.get("credential_id")
            )
        except GateTestnetExecutionServiceError as exc:
            raise GateTestnetExecutionServiceError(
                "TestNet execution requires immutable ledger scopes before submission"
            ) from exc
    if not isinstance(ledger_scope, GateTestnetLedgerScope) or not isinstance(
        persistence_scope, FillLedgerPersistenceScope
    ):
        raise GateTestnetExecutionServiceError("typed immutable ledger scopes are required")
    request = build_gate_testnet_execution_request(payload, graph)
    client = client_factory(request)
    worker = worker_factory(client)
    if not isinstance(worker, GateTestnetExecutionWorker):
        raise GateTestnetExecutionServiceError("typed TestNet worker is required")
    return result, worker.execute(
        connection,
        graph,
        result.admission,
        request,
        ledger_scope=ledger_scope,
        persistence_scope=persistence_scope,
        ledger_repository=ledger_repository,
        attempt_facts=attempt_facts,
        attempt_repository=attempt_repository,
        exchange_order_repository=exchange_order_repository,
    )


def cancel_gate_testnet_payload_caller_owned(
    connection: object,
    payload: Mapping[str, Any],
    *,
    tenant_id: int,
    actor_id: str,
    client_factory: Callable[[GateTestnetCancelRequest], Any],
) -> tuple[Any, Any]:
    """Admit and confirm one venue cancel without transaction control.

    Only a stable venue order id is accepted. Economic/client-order lookup is
    intentionally left to the read/query boundary until its venue-specific
    evidence is complete; this prevents an ambiguous cancel from becoming a
    second order side effect.
    """

    result, graph = admit_runtime_entry_payload_caller_owned(connection, payload, tenant_id=tenant_id, actor_id=actor_id)
    if result.admission is None or graph is None or result.admission.disposition is not EntryAdmissionDisposition.CREATED:
        raise GateTestnetExecutionServiceError("cancel requires a new CREATED admission")
    if graph.specification.action is not OrderAction.CANCEL:
        raise GateTestnetExecutionServiceError("cancel seam requires CANCEL action")
    intent = graph.specification.economic_intent
    if intent.cancel_target_kind is not CancelTargetKind.VENUE_ORDER_ID or not intent.cancel_target_id:
        raise GateTestnetExecutionServiceError("Gate TestNet cancel requires VENUE_ORDER_ID")
    cancel_request = GateTestnetCancelRequest(
        graph.specification.instrument_id,
        _market(graph.specification.market_type),
        graph.specification.account_scope,
        intent.cancel_target_id,
    )
    client = client_factory(cancel_request)
    cancel = getattr(client, "cancel_and_confirm", None)
    if not callable(cancel):
        raise GateTestnetExecutionServiceError("typed Gate cancel client is required")
    try:
        receipt = cancel(instrument_id=cancel_request.instrument_id, exchange_order_id=cancel_request.exchange_order_id)
    except GateTestnetExecutionServiceError:
        raise
    except Exception as exc:
        raise GateTestnetExecutionServiceError("Gate TestNet cancel failed") from exc
    return result, receipt


__all__ = [
    "GateTestnetExecutionServiceError",
    "GateTestnetCancelRequest",
    "build_gate_testnet_execution_request",
    "build_gate_testnet_execution_ledger_scopes",
    "execute_gate_testnet_payload_caller_owned",
    "cancel_gate_testnet_payload_caller_owned",
]
