"""Deterministic, non-live PAPER execution facts.

These contracts are deliberately independent from the legacy agent paper table.
They provide the durable shape used by the product rehearsal and recovery
path.  They never contain credentials and never grant exchange authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import uuid
from typing import Any


PAPER_EXECUTION_CONTRACT_VERSION = "paper-execution-v1"


class PaperExecutionContractError(ValueError):
    """Invalid or incomplete durable PAPER facts."""


class PaperExecutionStatus(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PaperExecutionEventType(str, Enum):
    SUBMITTED = "SUBMITTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


def _text(value: Any, name: str, *, upper: bool = False, lower: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or not value.isascii() or any(ch.isspace() for ch in value):
        raise PaperExecutionContractError(f"{name} must be canonical ASCII text")
    result = value
    if upper:
        result = result.upper()
    if lower:
        result = result.lower()
    return result


def _uuid(value: Any, name: str) -> str:
    try:
        return str(uuid.UUID(_text(value, name)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PaperExecutionContractError(f"{name} must be a UUID") from exc


def _decimal(value: Any, name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (bool, float)):
        raise PaperExecutionContractError(f"{name} rejects float/bool input")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaperExecutionContractError(f"{name} must be Decimal-compatible") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (non_negative and parsed < 0):
        raise PaperExecutionContractError(f"{name} has invalid numeric bounds")
    return parsed


def _utc(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise PaperExecutionContractError(f"{name} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PaperExecutionOrder:
    order_id: str
    user_id: int
    idempotency_key: str
    request_fingerprint: str
    market: str
    symbol: str
    market_type: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    status: PaperExecutionStatus
    created_at: datetime
    fill_quantity: Decimal = Decimal("0")
    fill_price: Decimal | None = None
    fee_amount: Decimal = Decimal("0")
    fee_asset: str = ""
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        order_id = _uuid(self.order_id, "order_id")
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int) or self.user_id <= 0:
            raise PaperExecutionContractError("user_id must be a positive integer")
        key = _text(self.idempotency_key, "idempotency_key")
        request = _text(self.request_fingerprint, "request_fingerprint")
        market = _text(self.market, "market", lower=True)
        symbol = _text(self.symbol, "symbol", upper=True)
        market_type = _text(self.market_type, "market_type", lower=True)
        if market_type not in {"spot", "perpetual"}:
            raise PaperExecutionContractError("market_type is unsupported")
        side = _text(self.side, "side", upper=True)
        if side not in {"BUY", "SELL"}:
            raise PaperExecutionContractError("side is unsupported")
        order_type = _text(self.order_type, "order_type", upper=True)
        if order_type not in {"MARKET", "LIMIT"}:
            raise PaperExecutionContractError("order_type is unsupported")
        limit = None if self.limit_price is None else _decimal(self.limit_price, "limit_price", positive=True)
        if order_type == "LIMIT" and limit is None:
            raise PaperExecutionContractError("limit orders require limit_price")
        quantity = _decimal(self.quantity, "quantity", positive=True)
        fill_quantity = _decimal(self.fill_quantity, "fill_quantity", non_negative=True)
        if fill_quantity > quantity:
            raise PaperExecutionContractError("fill_quantity cannot exceed quantity")
        fill_price = None if self.fill_price is None else _decimal(self.fill_price, "fill_price", positive=True)
        fee = _decimal(self.fee_amount, "fee_amount", non_negative=True)
        fee_asset = "" if self.fee_asset == "" else _text(self.fee_asset, "fee_asset", upper=True)
        if self.status in {PaperExecutionStatus.PARTIALLY_FILLED, PaperExecutionStatus.FILLED} and fill_price is None:
            raise PaperExecutionContractError("filled PAPER order requires fill_price")
        created_at = _utc(self.created_at, "created_at")
        material = {
            "version": PAPER_EXECUTION_CONTRACT_VERSION,
            "order_id": order_id, "user_id": self.user_id, "idempotency_key": key,
            "request_fingerprint": request, "market": market, "symbol": symbol,
            "market_type": market_type, "side": side, "order_type": order_type,
            "quantity": format(quantity.normalize(), "f"),
            "limit_price": None if limit is None else format(limit.normalize(), "f"),
        }
        fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(self, "request_fingerprint", request)
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "market_type", market_type)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "order_type", order_type)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "limit_price", limit)
        object.__setattr__(self, "fill_quantity", fill_quantity)
        object.__setattr__(self, "fill_price", fill_price)
        object.__setattr__(self, "fee_amount", fee)
        object.__setattr__(self, "fee_asset", fee_asset)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "fingerprint", fingerprint)


@dataclass(frozen=True, slots=True)
class PaperExecutionFill:
    fill_id: str
    order_id: str
    quantity: Decimal
    price: Decimal
    fee_amount: Decimal
    fee_asset: str
    occurred_at: datetime
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        fill_id = _uuid(self.fill_id, "fill_id")
        order_id = _uuid(self.order_id, "order_id")
        quantity = _decimal(self.quantity, "quantity", positive=True)
        price = _decimal(self.price, "price", positive=True)
        fee = _decimal(self.fee_amount, "fee_amount", non_negative=True)
        asset = _text(self.fee_asset, "fee_asset", upper=True)
        occurred_at = _utc(self.occurred_at, "occurred_at")
        material = {"version": PAPER_EXECUTION_CONTRACT_VERSION, "fill_id": fill_id, "order_id": order_id,
                    "quantity": format(quantity.normalize(), "f"), "price": format(price.normalize(), "f"),
                    "fee_amount": format(fee.normalize(), "f"), "fee_asset": asset, "occurred_at": occurred_at.isoformat()}
        object.__setattr__(self, "fill_id", fill_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "fee_amount", fee)
        object.__setattr__(self, "fee_asset", asset)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "fingerprint", hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest())


@dataclass(frozen=True, slots=True)
class PaperExecutionOrderEvent:
    event_id: str
    order_id: str
    event_seq: int
    event_type: PaperExecutionEventType
    occurred_at: datetime
    event_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        event_id = _uuid(self.event_id, "event_id")
        order_id = _uuid(self.order_id, "order_id")
        if isinstance(self.event_seq, bool) or not isinstance(self.event_seq, int) or self.event_seq < 1:
            raise PaperExecutionContractError("event_seq must be a positive integer")
        if not isinstance(self.event_type, PaperExecutionEventType):
            raise PaperExecutionContractError("event_type must be typed")
        occurred_at = _utc(self.occurred_at, "occurred_at")
        material = {"version": PAPER_EXECUTION_CONTRACT_VERSION, "event_id": event_id, "order_id": order_id,
                    "event_seq": self.event_seq, "event_type": self.event_type.value, "occurred_at": occurred_at.isoformat()}
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "order_id", order_id)
        object.__setattr__(self, "occurred_at", occurred_at)
        object.__setattr__(self, "event_fingerprint", hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest())


__all__ = ["PAPER_EXECUTION_CONTRACT_VERSION", "PaperExecutionContractError", "PaperExecutionStatus", "PaperExecutionEventType", "PaperExecutionOrder", "PaperExecutionFill", "PaperExecutionOrderEvent"]
