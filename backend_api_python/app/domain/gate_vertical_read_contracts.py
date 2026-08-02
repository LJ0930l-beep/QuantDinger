"""Pure Gate vertical read/evidence contracts (GATE-00..03).

These values describe supplied evidence only.  They do not create clients,
read credentials, contact Gate, or authorize order submission.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Tuple

from .multi_asset_capability_contracts import (
    AssetMarketType,
    AssetProduct,
    CapabilityEnvironment,
    MultiAssetCapabilityError,
    MultiAssetCapabilityMatrix,
    UnsupportedCapability,
)


GATE_VERTICAL_CONTRACT_VERSION = "gate-vertical-read-v1"


class GateVerticalContractError(ValueError):
    """Base error for invalid Gate vertical facts."""


class GatePermission(str, Enum):
    READ_MARKET = "read_market"
    READ_ACCOUNT = "read_account"
    READ_ORDER = "read_order"
    READ_FILL = "read_fill"
    WRITE_ORDER = "write_order"


class GateMarginMode(str, Enum):
    CROSS = "cross"
    ISOLATED = "isolated"


class GatePositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    NET = "net"


class GateOrderStatus(str, Enum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class GateOrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


def _decimal(value: Any, field: str, *, non_negative: bool = False, positive: bool = False) -> Decimal:
    if isinstance(value, float):
        raise GateVerticalContractError(f"{field} rejects float input")
    if isinstance(value, bool):
        raise GateVerticalContractError(f"{field} must be Decimal-compatible")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise GateVerticalContractError(f"{field} is not a valid decimal") from exc
    if not result.is_finite():
        raise GateVerticalContractError(f"{field} must be finite")
    if non_negative and result < 0:
        raise GateVerticalContractError(f"{field} must be non-negative")
    if positive and result <= 0:
        raise GateVerticalContractError(f"{field} must be positive")
    return result


def _utc(value: datetime, field: str = "observed_at") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GateVerticalContractError(f"{field} must be timezone-aware UTC")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateVerticalContractError(f"{field} must use zero UTC offset")
    return value.astimezone(timezone.utc)


def _text(value: str, field: str, *, lower: bool = False) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise GateVerticalContractError(f"{field} must be canonical text")
    if any(ord(ch) > 127 or ch.isspace() for ch in value):
        raise GateVerticalContractError(f"{field} must be ASCII without whitespace")
    return value.lower() if lower else value


@dataclass(frozen=True)
class GateAuthFacts:
    """Explicit permission evidence for one Gate read scope."""

    venue_id: str
    market_type: AssetMarketType
    environment: CapabilityEnvironment
    account_scope: str
    credential_ref: str
    permissions: Tuple[GatePermission, ...]
    evidence_version: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if _text(self.venue_id, "venue_id", lower=True) != "gate":
            raise GateVerticalContractError("only Gate evidence is accepted")
        if not isinstance(self.market_type, AssetMarketType):
            raise GateVerticalContractError("market_type must be typed")
        if self.environment is CapabilityEnvironment.DISABLED:
            raise GateVerticalContractError("disabled environment cannot carry auth facts")
        if self.environment not in (CapabilityEnvironment.PAPER, CapabilityEnvironment.SHADOW, CapabilityEnvironment.TESTNET):
            raise GateVerticalContractError("unsupported Gate environment")
        _text(self.account_scope, "account_scope")
        ref = _text(self.credential_ref, "credential_ref")
        if len(ref) > 128 or any(ch in ref for ch in ("=", ":", "/")):
            raise GateVerticalContractError("credential_ref must be opaque and non-sensitive")
        if any(not isinstance(item, GatePermission) for item in self.permissions):
            raise GateVerticalContractError("permissions must be typed")
        if len(set(self.permissions)) != len(self.permissions):
            raise GateVerticalContractError("permissions must be unique")
        if GatePermission.WRITE_ORDER in self.permissions:
            raise GateVerticalContractError("Gate read contract cannot authorize writes")
        if not _text(self.evidence_version, "evidence_version"):
            raise GateVerticalContractError("evidence_version is required")
        object.__setattr__(self, "observed_at", _utc(self.observed_at))


@dataclass(frozen=True)
class GateBalanceFact:
    venue_id: str
    market_type: AssetMarketType
    account_scope: str
    asset: str
    total: Decimal
    available: Decimal
    locked: Decimal
    valuation_ccy: str
    observed_at: datetime
    source_event_id: str
    evidence_hash: str

    def __post_init__(self) -> None:
        if _text(self.venue_id, "venue_id", lower=True) != "gate":
            raise GateVerticalContractError("balance venue must be gate")
        if not isinstance(self.market_type, AssetMarketType):
            raise GateVerticalContractError("balance market_type must be typed")
        _text(self.account_scope, "account_scope")
        asset = _text(self.asset, "asset").upper()
        ccy = _text(self.valuation_ccy, "valuation_ccy").upper()
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "valuation_ccy", ccy)
        total = _decimal(self.total, "total", non_negative=True)
        available = _decimal(self.available, "available", non_negative=True)
        locked = _decimal(self.locked, "locked", non_negative=True)
        if available + locked != total:
            raise GateVerticalContractError("available plus locked must equal total")
        object.__setattr__(self, "total", total)
        object.__setattr__(self, "available", available)
        object.__setattr__(self, "locked", locked)
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        _text(self.source_event_id, "source_event_id")
        _text(self.evidence_hash, "evidence_hash")


@dataclass(frozen=True)
class GateInstrumentRuleSnapshot:
    venue_id: str
    market_type: AssetMarketType
    instrument_id: str
    tick_size: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    rule_version: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if _text(self.venue_id, "venue_id", lower=True) != "gate":
            raise GateVerticalContractError("instrument venue must be gate")
        if not isinstance(self.market_type, AssetMarketType):
            raise GateVerticalContractError("instrument market_type must be typed")
        _text(self.instrument_id, "instrument_id")
        for name, value, positive in (
            ("tick_size", self.tick_size, True),
            ("quantity_step", self.quantity_step, True),
            ("minimum_quantity", self.minimum_quantity, False),
            ("minimum_notional", self.minimum_notional, False),
        ):
            normalized = _decimal(value, name, non_negative=True, positive=positive)
            object.__setattr__(self, name, normalized)
        _text(self.rule_version, "rule_version")
        object.__setattr__(self, "observed_at", _utc(self.observed_at))


@dataclass(frozen=True)
class GatePositionFact:
    venue_id: str
    market_type: AssetMarketType
    account_scope: str
    instrument_id: str
    side: GatePositionSide
    quantity: Decimal
    average_entry_price: Decimal
    mark_price: Decimal
    leverage: Decimal
    margin_mode: GateMarginMode
    observed_at: datetime
    source_event_id: str

    def __post_init__(self) -> None:
        if _text(self.venue_id, "venue_id", lower=True) != "gate":
            raise GateVerticalContractError("position venue must be gate")
        if not isinstance(self.market_type, AssetMarketType) or not isinstance(self.side, GatePositionSide):
            raise GateVerticalContractError("position enums must be typed")
        if not isinstance(self.margin_mode, GateMarginMode):
            raise GateVerticalContractError("margin_mode must be typed")
        _text(self.account_scope, "account_scope")
        _text(self.instrument_id, "instrument_id")
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", non_negative=True))
        object.__setattr__(self, "average_entry_price", _decimal(self.average_entry_price, "average_entry_price", positive=True))
        object.__setattr__(self, "mark_price", _decimal(self.mark_price, "mark_price", positive=True))
        object.__setattr__(self, "leverage", _decimal(self.leverage, "leverage", positive=True))
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        _text(self.source_event_id, "source_event_id")


@dataclass(frozen=True)
class GateOrderFact:
    """Read-only normalized order evidence; never authorizes a write."""

    venue_id: str
    market_type: AssetMarketType
    account_scope: str
    instrument_id: str
    exchange_order_id: str
    client_order_id: str | None
    side: GateOrderSide
    status: GateOrderStatus
    quantity: Decimal
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    observed_at: datetime
    source_event_id: str
    raw_status: str = ""
    finish_reason: str | None = None

    def __post_init__(self) -> None:
        if _text(self.venue_id, "venue_id", lower=True) != "gate" or not isinstance(self.market_type, AssetMarketType):
            raise GateVerticalContractError("order venue and market_type must be typed")
        if not isinstance(self.side, GateOrderSide) or not isinstance(self.status, GateOrderStatus):
            raise GateVerticalContractError("order side and status must be typed")
        for field in ("account_scope", "instrument_id", "exchange_order_id", "source_event_id"):
            _text(getattr(self, field), field)
        if self.client_order_id is not None:
            _text(self.client_order_id, "client_order_id")
        quantity = _decimal(self.quantity, "quantity", positive=True)
        filled = _decimal(self.filled_quantity, "filled_quantity", non_negative=True)
        if filled > quantity:
            raise GateVerticalContractError("filled_quantity cannot exceed quantity")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "filled_quantity", filled)
        if self.average_fill_price is not None:
            object.__setattr__(self, "average_fill_price", _decimal(self.average_fill_price, "average_fill_price", positive=True))
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if self.raw_status:
            _text(self.raw_status, "raw_status")
        if self.finish_reason is not None:
            _text(self.finish_reason, "finish_reason")


@dataclass(frozen=True)
class GateFillFact:
    """Stable venue fill evidence; missing venue fill IDs fail closed."""

    venue_id: str
    market_type: AssetMarketType
    account_scope: str
    instrument_id: str
    exchange_order_id: str
    venue_fill_id: str
    side: GateOrderSide
    quantity: Decimal
    price: Decimal
    fee_asset: str | None
    fee_amount: Decimal | None
    observed_at: datetime
    source_event_id: str

    def __post_init__(self) -> None:
        if _text(self.venue_id, "venue_id", lower=True) != "gate" or not isinstance(self.market_type, AssetMarketType):
            raise GateVerticalContractError("fill venue and market_type must be typed")
        if not isinstance(self.side, GateOrderSide):
            raise GateVerticalContractError("fill side must be typed")
        for field in ("account_scope", "instrument_id", "exchange_order_id", "venue_fill_id", "source_event_id"):
            _text(getattr(self, field), field)
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))
        object.__setattr__(self, "price", _decimal(self.price, "price", positive=True))
        if self.fee_asset is None:
            if self.fee_amount is not None:
                raise GateVerticalContractError("fee_amount requires fee_asset")
        else:
            asset = _text(self.fee_asset, "fee_asset").upper()
            amount = _decimal(self.fee_amount, "fee_amount", non_negative=True)
            object.__setattr__(self, "fee_asset", asset)
            object.__setattr__(self, "fee_amount", amount)
        object.__setattr__(self, "observed_at", _utc(self.observed_at))


def gate_read_fingerprint(value: Any) -> str:
    """Return a stable SHA-256 fingerprint for supplied typed evidence."""

    def normalize(item: Any) -> Any:
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, Decimal):
            # Decimal values are numeric facts: equivalent scales must produce
            # the same canonical identity while remaining non-exponential.
            normalized = item.normalize()
            return format(normalized, "f")
        if isinstance(item, datetime):
            return _utc(item).isoformat()
        if hasattr(item, "__dataclass_fields__"):
            return normalize(asdict(item))
        if isinstance(item, dict):
            return {str(k): normalize(v) for k, v in sorted(item.items(), key=lambda pair: str(pair[0]))}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        return item

    payload = json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_gate_capability(
    matrix: MultiAssetCapabilityMatrix,
    auth: GateAuthFacts,
    permission: GatePermission,
) -> None:
    """Require an exact profile and permission; no profile inheritance."""

    if not isinstance(auth, GateAuthFacts) or not isinstance(permission, GatePermission):
        raise GateVerticalContractError("typed Gate auth and permission are required")
    try:
        product = AssetProduct(auth.market_type.value)
    except ValueError as exc:
        raise UnsupportedCapability("market type has no evidenced Gate product profile") from exc
    profile = matrix.resolve("gate", product, auth.market_type, auth.environment)
    if permission not in auth.permissions:
        raise UnsupportedCapability("permission is not present in the supplied auth facts")
    if permission is GatePermission.WRITE_ORDER or profile.supports_write or profile.auto_live_eligible:
        raise UnsupportedCapability("write capability is not eligible in this contract")
    if permission is GatePermission.READ_MARKET and not profile.supports_public_market_data:
        raise UnsupportedCapability("market read capability is not evidenced")
    if permission is GatePermission.READ_ACCOUNT and not profile.supports_account_reads:
        raise UnsupportedCapability("account read capability is not evidenced")
    if permission is GatePermission.READ_ORDER and not profile.supports_order_reads:
        raise UnsupportedCapability("order read capability is not evidenced")
    if permission is GatePermission.READ_FILL and not profile.supports_fill_reads:
        raise UnsupportedCapability("fill read capability is not evidenced")


__all__ = [
    "GATE_VERTICAL_CONTRACT_VERSION",
    "GateAuthFacts",
    "GateBalanceFact",
    "GateInstrumentRuleSnapshot",
    "GateFillFact",
    "GateOrderFact",
    "GateOrderSide",
    "GateOrderStatus",
    "GateMarginMode",
    "GatePermission",
    "GatePositionFact",
    "GatePositionSide",
    "GateVerticalContractError",
    "gate_read_fingerprint",
    "require_gate_capability",
]
