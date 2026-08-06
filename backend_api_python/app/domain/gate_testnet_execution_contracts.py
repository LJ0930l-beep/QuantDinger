"""Deterministic, fixture-only Gate TestNet execution evidence.

This module models an order lifecycle without creating a venue client, reading
credentials, opening a socket, or authorizing a write.  It is deliberately
useful enough to exercise the same typed order/fill facts used by the
read-only Gate surface while remaining a local simulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json

from .gate_vertical_read_contracts import (
    GateFillFact,
    GateOrderFact,
    GateOrderSide,
    GateOrderStatus,
)
from .multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment


GATE_TESTNET_EXECUTION_CONTRACT_VERSION = "gate-testnet-execution-rehearsal-v1"


class GateTestnetExecutionContractError(ValueError):
    """The fixture execution facts are invalid or incomplete."""


class GateExecutionKind(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class GateTriggerDirection(str, Enum):
    """Venue-neutral trigger rule retained in TestNet evidence."""

    AT_OR_ABOVE = "AT_OR_ABOVE"
    AT_OR_BELOW = "AT_OR_BELOW"


class GateTriggerPriceType(str, Enum):
    """The price source selected by the canonical request."""

    LAST = "LAST"
    MARK = "MARK"
    INDEX = "INDEX"


class GateExecutionDisposition(str, Enum):
    ACCEPTED = "ACCEPTED"
    REPLAYED = "REPLAYED"
    REJECTED = "REJECTED"


def _text(value: object, field_name: str, *, lower: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii():
        raise GateTestnetExecutionContractError(f"{field_name} must be canonical ASCII text")
    if any(char.isspace() for char in value):
        raise GateTestnetExecutionContractError(f"{field_name} must not contain whitespace")
    return value.lower() if lower else value


def _decimal(value: object, field_name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise GateTestnetExecutionContractError(f"{field_name} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateTestnetExecutionContractError(f"{field_name} must be Decimal-compatible") from exc
    if not result.is_finite():
        raise GateTestnetExecutionContractError(f"{field_name} must be finite")
    if positive and result <= 0:
        raise GateTestnetExecutionContractError(f"{field_name} must be positive")
    if non_negative and result < 0:
        raise GateTestnetExecutionContractError(f"{field_name} must be non-negative")
    return result


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateTestnetExecutionContractError("observed_at must use zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class GateTestnetExecutionRequest:
    """A complete local execution simulation request."""

    instrument_id: str
    market_type: AssetMarketType
    account_scope: str
    side: GateOrderSide
    quantity: Decimal
    reference_price: Decimal
    execution_kind: GateExecutionKind = GateExecutionKind.MARKET
    limit_price: Decimal | None = None
    trigger_price: Decimal | None = None
    trigger_direction: GateTriggerDirection | None = None
    trigger_price_type: GateTriggerPriceType | None = None
    fill_ratio: Decimal = Decimal("1")
    fee_rate: Decimal = Decimal("0.001")
    fee_asset: str = "USDT"
    client_order_id: str = "fixture-client-order-1"
    reduce_only: bool = False
    observed_at: datetime = datetime(2026, 1, 1, tzinfo=timezone.utc)
    environment: CapabilityEnvironment = CapabilityEnvironment.TESTNET

    def __post_init__(self) -> None:
        instrument = _text(self.instrument_id, "instrument_id")
        object.__setattr__(self, "instrument_id", instrument)
        if not isinstance(self.market_type, AssetMarketType) or self.market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
            raise GateTestnetExecutionContractError("only Spot and Perpetual TestNet simulation is supported")
        object.__setattr__(self, "account_scope", _text(self.account_scope, "account_scope"))
        if not isinstance(self.side, GateOrderSide):
            raise GateTestnetExecutionContractError("side must be typed")
        if not isinstance(self.execution_kind, GateExecutionKind):
            raise GateTestnetExecutionContractError("execution_kind must be typed")
        if not isinstance(self.environment, CapabilityEnvironment) or self.environment is not CapabilityEnvironment.TESTNET:
            raise GateTestnetExecutionContractError("execution rehearsal is TESTNET-only")
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        object.__setattr__(self, "reference_price", _decimal(self.reference_price, "reference_price", positive=True))
        is_stop = self.execution_kind in (GateExecutionKind.STOP_MARKET, GateExecutionKind.STOP_LIMIT)
        needs_limit = self.execution_kind in (GateExecutionKind.LIMIT, GateExecutionKind.STOP_LIMIT)
        if needs_limit:
            if self.limit_price is None:
                raise GateTestnetExecutionContractError("LIMIT/STOP_LIMIT requires limit_price")
            object.__setattr__(self, "limit_price", _decimal(self.limit_price, "limit_price", positive=True))
        elif self.limit_price is not None:
            raise GateTestnetExecutionContractError("MARKET/STOP_MARKET cannot carry limit_price")
        if is_stop:
            if self.trigger_price is None or not isinstance(self.trigger_direction, GateTriggerDirection) or not isinstance(self.trigger_price_type, GateTriggerPriceType):
                raise GateTestnetExecutionContractError("STOP orders require typed trigger facts")
            object.__setattr__(self, "trigger_price", _decimal(self.trigger_price, "trigger_price", positive=True))
        elif any(value is not None for value in (self.trigger_price, self.trigger_direction, self.trigger_price_type)):
            raise GateTestnetExecutionContractError("non-STOP orders cannot carry trigger facts")
        ratio = _decimal(self.fill_ratio, "fill_ratio", non_negative=True)
        if ratio > 1:
            raise GateTestnetExecutionContractError("fill_ratio cannot exceed 1")
        object.__setattr__(self, "fill_ratio", ratio)
        object.__setattr__(self, "fee_rate", _decimal(self.fee_rate, "fee_rate", non_negative=True))
        object.__setattr__(self, "fee_asset", _text(self.fee_asset, "fee_asset").upper())
        object.__setattr__(self, "client_order_id", _text(self.client_order_id, "client_order_id"))
        if not isinstance(self.reduce_only, bool):
            raise GateTestnetExecutionContractError("reduce_only must be boolean")
        object.__setattr__(self, "observed_at", _utc(self.observed_at))

    @property
    def request_fingerprint(self) -> str:
        material = {
            "version": GATE_TESTNET_EXECUTION_CONTRACT_VERSION,
            "instrument_id": self.instrument_id,
            "market_type": self.market_type.value,
            "account_scope": self.account_scope,
            "side": self.side.value,
            "quantity": format(self.quantity.normalize(), "f"),
            "reference_price": format(self.reference_price.normalize(), "f"),
            "execution_kind": self.execution_kind.value,
            "limit_price": None if self.limit_price is None else format(self.limit_price.normalize(), "f"),
            "trigger_price": None if self.trigger_price is None else format(self.trigger_price.normalize(), "f"),
            "trigger_direction": None if self.trigger_direction is None else self.trigger_direction.value,
            "trigger_price_type": None if self.trigger_price_type is None else self.trigger_price_type.value,
            "fill_ratio": format(self.fill_ratio.normalize(), "f"),
            "fee_rate": format(self.fee_rate.normalize(), "f"),
            "fee_asset": self.fee_asset,
            "client_order_id": self.client_order_id,
            "reduce_only": self.reduce_only,
            "observed_at": self.observed_at.isoformat(),
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class GateTestnetExecutionReceipt:
    request: GateTestnetExecutionRequest
    disposition: GateExecutionDisposition
    order: GateOrderFact
    fills: tuple[GateFillFact, ...]
    fee_amount: Decimal
    lifecycle_fingerprint: str = field(init=False)
    network_access: bool = False
    writes_enabled: bool = False
    live_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.request, GateTestnetExecutionRequest) or not isinstance(self.disposition, GateExecutionDisposition):
            raise GateTestnetExecutionContractError("request and disposition must be typed")
        if not isinstance(self.order, GateOrderFact) or not isinstance(self.fills, tuple) or any(not isinstance(item, GateFillFact) for item in self.fills):
            raise GateTestnetExecutionContractError("order and fills must use Gate read facts")
        if self.order.client_order_id != self.request.client_order_id or self.order.instrument_id != self.request.instrument_id:
            raise GateTestnetExecutionContractError("order scope does not match request")
        for fill in self.fills:
            if (
                fill.venue_id != self.order.venue_id
                or fill.market_type is not self.order.market_type
                or fill.account_scope != self.order.account_scope
                or fill.instrument_id != self.order.instrument_id
                or fill.exchange_order_id != self.order.exchange_order_id
                or fill.side is not self.order.side
            ):
                raise GateTestnetExecutionContractError("fill scope does not match order scope")
        if self.order.filled_quantity != sum((item.quantity for item in self.fills), Decimal("0")):
            raise GateTestnetExecutionContractError("order/fill quantity mismatch")
        fee = _decimal(self.fee_amount, "fee_amount", non_negative=True)
        object.__setattr__(self, "fee_amount", fee)
        if self.network_access or self.writes_enabled or self.live_enabled:
            raise GateTestnetExecutionContractError("fixture receipt cannot authorize network or live writes")
        material = {
            "version": GATE_TESTNET_EXECUTION_CONTRACT_VERSION,
            "request": self.request.request_fingerprint,
            "disposition": self.disposition.value,
            "order": self.order.exchange_order_id,
            "status": self.order.status.value,
            "filled": format(self.order.filled_quantity.normalize(), "f"),
            "fills": [item.venue_fill_id for item in self.fills],
            "fee": format(fee.normalize(), "f"),
            "fee_asset": self.request.fee_asset,
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "lifecycle_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": GATE_TESTNET_EXECUTION_CONTRACT_VERSION,
            "environment": self.request.environment.value,
            "network_access": False,
            "writes_enabled": False,
            "live_enabled": False,
            "disposition": self.disposition.value,
            "request_fingerprint": self.request.request_fingerprint,
            "reduce_only": self.request.reduce_only,
            "trigger_price": None if self.request.trigger_price is None else format(self.request.trigger_price.normalize(), "f"),
            "trigger_direction": None if self.request.trigger_direction is None else self.request.trigger_direction.value,
            "trigger_price_type": None if self.request.trigger_price_type is None else self.request.trigger_price_type.value,
            "lifecycle_fingerprint": self.lifecycle_fingerprint,
            "order": {
                "exchange_order_id": self.order.exchange_order_id,
                "client_order_id": self.order.client_order_id,
                "status": self.order.status.value,
                "quantity": format(self.order.quantity.normalize(), "f"),
                "filled_quantity": format(self.order.filled_quantity.normalize(), "f"),
                "average_fill_price": None if self.order.average_fill_price is None else format(self.order.average_fill_price.normalize(), "f"),
            },
            "fills": [
                {"venue_fill_id": item.venue_fill_id, "quantity": format(item.quantity.normalize(), "f"), "price": format(item.price.normalize(), "f"), "fee_asset": item.fee_asset, "fee_amount": None if item.fee_amount is None else format(item.fee_amount.normalize(), "f")}
                for item in self.fills
            ],
            "fee_asset": self.request.fee_asset,
            "fee_amount": format(self.fee_amount.normalize(), "f"),
        }


def simulate_gate_testnet_execution(request: GateTestnetExecutionRequest) -> GateTestnetExecutionReceipt:
    """Produce deterministic order/fill facts; never calls a venue."""

    if not isinstance(request, GateTestnetExecutionRequest):
        raise GateTestnetExecutionContractError("request must be typed")
    order_id = "fixture-order-" + request.request_fingerprint[:20]
    triggered = True
    if request.execution_kind in (GateExecutionKind.STOP_MARKET, GateExecutionKind.STOP_LIMIT):
        assert request.trigger_price is not None and request.trigger_direction is not None
        triggered = (
            request.reference_price >= request.trigger_price
            if request.trigger_direction is GateTriggerDirection.AT_OR_ABOVE
            else request.reference_price <= request.trigger_price
        )
    fill_quantity = request.quantity * request.fill_ratio if triggered else Decimal("0")
    fill_price = request.limit_price or request.reference_price
    fee_amount = fill_quantity * fill_price * request.fee_rate
    if not triggered:
        status = GateOrderStatus.OPEN
    elif fill_quantity == 0:
        status = GateOrderStatus.CANCELLED
    elif fill_quantity < request.quantity:
        status = GateOrderStatus.PARTIALLY_FILLED
    else:
        status = GateOrderStatus.FILLED
    fills: tuple[GateFillFact, ...] = ()
    if fill_quantity > 0:
        fills = (GateFillFact(
            "gate", request.market_type, request.account_scope, request.instrument_id,
            order_id, "fixture-fill-" + request.request_fingerprint[:20], request.side,
            fill_quantity, fill_price, request.fee_asset, fee_amount,
            request.observed_at, "fixture-fill-event-" + request.request_fingerprint[:20],
        ),)
    order = GateOrderFact(
        "gate", request.market_type, request.account_scope, request.instrument_id,
        order_id, request.client_order_id, request.side, status,
        request.quantity, fill_quantity, fill_price if fill_quantity > 0 else None,
        request.observed_at, "fixture-order-event-" + request.request_fingerprint[:20],
        raw_status=status.value, finish_reason="fixture_rehearsal",
    )
    return GateTestnetExecutionReceipt(request, GateExecutionDisposition.ACCEPTED, order, fills, fee_amount)


__all__ = [
    "GATE_TESTNET_EXECUTION_CONTRACT_VERSION",
    "GateExecutionDisposition",
    "GateExecutionKind",
    "GateTriggerDirection",
    "GateTriggerPriceType",
    "GateTestnetExecutionContractError",
    "GateTestnetExecutionReceipt",
    "GateTestnetExecutionRequest",
    "simulate_gate_testnet_execution",
]
