"""Typed, read-only facts for the durable PAPER account surface.

This view is intentionally backed by existing paper-order facts.  It does
not infer exchange state, expose credentials, or grant any execution
authority; missing or malformed rows fail closed at the repository boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
from typing import Any


READONLY_PAPER_ACCOUNT_CONTRACT_VERSION = "readonly-paper-account-v1"
PAPER_POSITION_PROJECTION_VERSION = "paper-position-projection-v1"


class ReadonlyPaperAccountError(ValueError):
    """Invalid or incomplete persisted PAPER facts."""


class PaperOrderStatus(str, Enum):
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PaperPositionProjectionError(ReadonlyPaperAccountError):
    """Filled paper facts cannot form a deterministic position projection."""


@dataclass(frozen=True, slots=True)
class ReadonlyPaperPositionEstimate:
    market: str
    symbol: str
    signed_quantity: Decimal
    average_entry_price: Decimal | None
    realized_pnl: Decimal
    projection_status: str = "DERIVED_PAPER_ESTIMATE"

    def __post_init__(self) -> None:
        _text(self.market, "market")
        _text(self.symbol, "symbol")
        quantity = _decimal(self.signed_quantity, "signed_quantity")
        average = None if self.average_entry_price is None else _decimal(self.average_entry_price, "average_entry_price", positive=True)
        realized = _decimal(self.realized_pnl, "realized_pnl")
        if self.projection_status != "DERIVED_PAPER_ESTIMATE":
            raise PaperPositionProjectionError("unsupported paper position projection status")
        object.__setattr__(self, "signed_quantity", quantity)
        object.__setattr__(self, "average_entry_price", average)
        object.__setattr__(self, "realized_pnl", realized)


def project_paper_positions(orders: tuple["ReadonlyPaperOrderFact", ...]) -> tuple[ReadonlyPaperPositionEstimate, ...]:
    """Project filled paper orders into deterministic, explicitly derived positions.

    This is not an account-balance or fee ledger.  Every filled order must
    carry a fill price; otherwise the projection refuses to guess.
    """
    if not isinstance(orders, tuple) or any(not isinstance(item, ReadonlyPaperOrderFact) for item in orders):
        raise PaperPositionProjectionError("orders must be typed")
    state: dict[tuple[str, str], dict[str, Decimal | None]] = {}
    for order in sorted(orders, key=lambda item: (item.created_at, item.order_uid)):
        if order.status is not PaperOrderStatus.FILLED:
            continue
        if order.fill_price is None:
            raise PaperPositionProjectionError("filled paper order requires fill_price for projection")
        key = (order.market, order.symbol)
        current = state.setdefault(key, {"quantity": Decimal("0"), "average": None, "realized": Decimal("0")})
        signed = order.quantity if order.side.lower() == "buy" else -order.quantity
        quantity = current["quantity"] or Decimal("0")
        average = current["average"]
        fill_price = order.fill_price
        if quantity == 0:
            current["quantity"] = signed
            current["average"] = fill_price
            continue
        same_direction = (quantity > 0 and signed > 0) or (quantity < 0 and signed < 0)
        if same_direction:
            current["average"] = ((abs(quantity) * (average or fill_price)) + (abs(signed) * fill_price)) / (abs(quantity) + abs(signed))
            current["quantity"] = quantity + signed
            continue
        closing = min(abs(quantity), abs(signed))
        if quantity > 0:
            current["realized"] = (current["realized"] or Decimal("0")) + (fill_price - (average or fill_price)) * closing
        else:
            current["realized"] = (current["realized"] or Decimal("0")) + ((average or fill_price) - fill_price) * closing
        remaining = quantity + signed
        current["quantity"] = remaining
        current["average"] = None if remaining == 0 else (average if (quantity > 0 and remaining > 0) or (quantity < 0 and remaining < 0) else fill_price)
    result = []
    for (market, symbol), values in sorted(state.items()):
        result.append(ReadonlyPaperPositionEstimate(market, symbol, values["quantity"] or Decimal("0"), values["average"], values["realized"] or Decimal("0")))
    return tuple(result)


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii() or any(ch.isspace() for ch in value):
        raise ReadonlyPaperAccountError(f"{field_name} must be canonical ASCII text")
    return value


def _decimal(value: Any, field_name: str, *, non_negative: bool = False, positive: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise ReadonlyPaperAccountError(f"{field_name} rejects float/bool input")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReadonlyPaperAccountError(f"{field_name} is not decimal") from exc
    if not parsed.is_finite() or (non_negative and parsed < 0) or (positive and parsed <= 0):
        raise ReadonlyPaperAccountError(f"{field_name} has invalid numeric bounds")
    return parsed


def _utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyPaperAccountError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReadonlyPaperOrderFact:
    order_uid: str
    market: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    fill_price: Decimal | None
    fill_value: Decimal | None
    status: PaperOrderStatus
    note: str
    created_at: datetime

    def __post_init__(self) -> None:
        _text(self.order_uid, "order_uid")
        _text(self.market, "market")
        _text(self.symbol, "symbol")
        side = _text(self.side, "side").lower()
        if side not in {"buy", "sell"}:
            raise ReadonlyPaperAccountError("side is unsupported")
        _text(self.order_type, "order_type")
        if not isinstance(self.status, PaperOrderStatus):
            raise ReadonlyPaperAccountError("status must be typed")
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        for name in ("limit_price", "fill_price", "fill_value"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _decimal(value, name, non_negative=True))
        if self.fill_value is not None and self.fill_price is None:
            raise ReadonlyPaperAccountError("fill_value requires fill_price")
        if not isinstance(self.note, str) or self.note.strip() != self.note or any(ord(ch) < 32 for ch in self.note):
            raise ReadonlyPaperAccountError("note must be canonical text")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class ReadonlyPaperAccountSnapshot:
    user_id: int
    orders: tuple[ReadonlyPaperOrderFact, ...]
    observed_at: datetime
    snapshot_fingerprint: str = field(init=False)
    positions: tuple[ReadonlyPaperPositionEstimate, ...] = field(init=False)

    def __post_init__(self) -> None:
        if isinstance(self.user_id, bool) or not isinstance(self.user_id, int) or self.user_id <= 0:
            raise ReadonlyPaperAccountError("user_id must be positive integer")
        if not isinstance(self.orders, tuple) or any(not isinstance(item, ReadonlyPaperOrderFact) for item in self.orders):
            raise ReadonlyPaperAccountError("orders must be a typed tuple")
        if len({item.order_uid for item in self.orders}) != len(self.orders):
            raise ReadonlyPaperAccountError("order_uid must be unique")
        observed = _utc(self.observed_at, "observed_at")
        if any(item.created_at > observed for item in self.orders):
            raise ReadonlyPaperAccountError("order cannot be newer than observed_at")
        try:
            projected_positions = project_paper_positions(self.orders)
        except PaperPositionProjectionError:
            raise
        object.__setattr__(self, "positions", projected_positions)
        object.__setattr__(self, "observed_at", observed)
        material = {
            "version": READONLY_PAPER_ACCOUNT_CONTRACT_VERSION,
            "position_projection_version": PAPER_POSITION_PROJECTION_VERSION,
            "user_id": self.user_id,
            "observed_at": observed.isoformat(),
            "orders": [
                {
                    "order_uid": item.order_uid,
                    "market": item.market,
                    "symbol": item.symbol,
                    "side": item.side,
                    "order_type": item.order_type,
                    "quantity": format(item.quantity.normalize(), "f"),
                    "limit_price": None if item.limit_price is None else format(item.limit_price.normalize(), "f"),
                    "fill_price": None if item.fill_price is None else format(item.fill_price.normalize(), "f"),
                    "fill_value": None if item.fill_value is None else format(item.fill_value.normalize(), "f"),
                    "status": item.status.value,
                    "note": item.note,
                    "created_at": item.created_at.isoformat(),
                }
                for item in self.orders
            ],
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "snapshot_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    @property
    def filled_count(self) -> int:
        return sum(1 for item in self.orders if item.status is PaperOrderStatus.FILLED)

    def to_public_dict(self) -> dict[str, Any]:
        def decimal_text(value: Decimal | None) -> str | None:
            return None if value is None else format(value.normalize(), "f")

        return {
            "contract_version": READONLY_PAPER_ACCOUNT_CONTRACT_VERSION,
            "status": "READY",
            "mode": "PAPER",
            "user_id": self.user_id,
            "observed_at": self.observed_at.isoformat(),
            "order_count": len(self.orders),
            "filled_count": self.filled_count,
            "position_projection_status": "DERIVED_PAPER_ESTIMATE",
            "position_projection_version": PAPER_POSITION_PROJECTION_VERSION,
            "positions": [
                {
                    "market": item.market,
                    "symbol": item.symbol,
                    "signed_quantity": decimal_text(item.signed_quantity),
                    "average_entry_price": decimal_text(item.average_entry_price),
                    "realized_pnl": decimal_text(item.realized_pnl),
                    "projection_status": item.projection_status,
                }
                for item in self.positions
            ],
            "fees_status": "UNAVAILABLE_NO_FEE_FACTS",
            "funding_status": "UNAVAILABLE_NO_FUNDING_FACTS",
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "orders": [
                {
                    "order_uid": item.order_uid,
                    "market": item.market,
                    "symbol": item.symbol,
                    "side": item.side,
                    "order_type": item.order_type,
                    "quantity": decimal_text(item.quantity),
                    "limit_price": decimal_text(item.limit_price),
                    "fill_price": decimal_text(item.fill_price),
                    "fill_value": decimal_text(item.fill_value),
                    "status": item.status.value,
                    "note": item.note,
                    "created_at": item.created_at.isoformat(),
                }
                for item in self.orders
            ],
            "live_enabled": False,
        }


__all__ = [
    "PaperOrderStatus",
    "PAPER_POSITION_PROJECTION_VERSION",
    "READONLY_PAPER_ACCOUNT_CONTRACT_VERSION",
    "ReadonlyPaperAccountError",
    "PaperPositionProjectionError",
    "ReadonlyPaperPositionEstimate",
    "ReadonlyPaperAccountSnapshot",
    "ReadonlyPaperOrderFact",
    "project_paper_positions",
]
