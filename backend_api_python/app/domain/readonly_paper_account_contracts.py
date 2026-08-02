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


class ReadonlyPaperAccountError(ValueError):
    """Invalid or incomplete persisted PAPER facts."""


class PaperOrderStatus(str, Enum):
    SUBMITTED = "submitted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


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
        object.__setattr__(self, "observed_at", observed)
        material = {
            "version": READONLY_PAPER_ACCOUNT_CONTRACT_VERSION,
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
    "READONLY_PAPER_ACCOUNT_CONTRACT_VERSION",
    "ReadonlyPaperAccountError",
    "ReadonlyPaperAccountSnapshot",
    "ReadonlyPaperOrderFact",
]
