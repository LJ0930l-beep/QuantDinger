"""Pure Gate delivery, equity-session, and options read facts (GATE-08..10)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .multi_asset_capability_contracts import AssetMarketType


GATE_EXTENDED_READ_CONTRACT_VERSION = "gate-extended-read-v1"


class GateExtendedReadError(ValueError):
    """Malformed, incomplete, or scope-inconsistent extended evidence."""


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) > 127 or c.isspace() for c in value): raise GateExtendedReadError(f"{field} must be canonical ASCII text")
    return value


def _decimal(value: Any, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (float, bool)): raise GateExtendedReadError(f"{field} rejects float/bool input")
    try: result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc: raise GateExtendedReadError(f"{field} is not a decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (non_negative and result < 0): raise GateExtendedReadError(f"{field} has invalid bounds")
    return result


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value): raise GateExtendedReadError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class GateDeliveryFact:
    instrument_id: str
    contract_size: Decimal
    expiry_at: datetime
    delivery_at: datetime
    delivery_price: Decimal | None
    observed_at: datetime
    source_event_id: str
    rule_version: str

    def __post_init__(self) -> None:
        for field in ("instrument_id", "source_event_id", "rule_version"): _text(getattr(self, field), field)
        object.__setattr__(self, "contract_size", _decimal(self.contract_size, "contract_size", positive=True)); expiry, delivery = _utc(self.expiry_at, "expiry_at"), _utc(self.delivery_at, "delivery_at")
        if delivery < expiry: raise GateExtendedReadError("delivery_at must follow expiry_at")
        object.__setattr__(self, "expiry_at", expiry); object.__setattr__(self, "delivery_at", delivery); object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.delivery_price is not None: object.__setattr__(self, "delivery_price", _decimal(self.delivery_price, "delivery_price", positive=True))


@dataclass(frozen=True)
class GateEquitySessionFact:
    instrument_id: str
    session_open: datetime
    session_close: datetime
    timezone_name: str
    corporate_action_snapshot_id: str
    observed_at: datetime
    source_event_id: str

    def __post_init__(self) -> None:
        for field in ("instrument_id", "timezone_name", "corporate_action_snapshot_id", "source_event_id"): _text(getattr(self, field), field)
        opened, closed = _utc(self.session_open, "session_open"), _utc(self.session_close, "session_close")
        if closed <= opened: raise GateExtendedReadError("session_close must follow session_open")
        object.__setattr__(self, "session_open", opened); object.__setattr__(self, "session_close", closed); object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))


@dataclass(frozen=True)
class GateOptionMarkFact:
    option_id: str
    underlying_id: str
    market_type: AssetMarketType
    right: OptionRight
    strike: Decimal
    expiry_at: datetime
    mark_price: Decimal
    implied_volatility: Decimal
    observed_at: datetime
    source_event_id: str
    rule_version: str

    def __post_init__(self) -> None:
        for field in ("option_id", "underlying_id", "source_event_id", "rule_version"): _text(getattr(self, field), field)
        if self.market_type is not AssetMarketType.OPTIONS or not isinstance(self.right, OptionRight): raise GateExtendedReadError("options mark requires typed options scope")
        for field in ("strike", "mark_price"): object.__setattr__(self, field, _decimal(getattr(self, field), field, positive=True))
        object.__setattr__(self, "implied_volatility", _decimal(self.implied_volatility, "implied_volatility", non_negative=True)); object.__setattr__(self, "expiry_at", _utc(self.expiry_at, "expiry_at")); object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))


def gate_extended_fingerprint(value: Any) -> str:
    def norm(item: Any) -> Any:
        if isinstance(item, Enum): return item.value
        if isinstance(item, Decimal): return format(item.normalize(), "f")
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "__dataclass_fields__"): return norm(asdict(item))
        if isinstance(item, dict): return {str(k): norm(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        return item
    return hashlib.sha256(json.dumps(norm(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


__all__ = ["GATE_EXTENDED_READ_CONTRACT_VERSION", "GateDeliveryFact", "GateEquitySessionFact", "GateExtendedReadError", "GateOptionMarkFact", "OptionRight", "gate_extended_fingerprint"]
