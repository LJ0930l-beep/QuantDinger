"""Pure Gate market-data evidence contracts for GATE-04 and GATE-05.

The module contains immutable, replayable read facts only.  It deliberately
has no client, network, persistence, scheduling, or order-submission code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Tuple

from .multi_asset_capability_contracts import AssetMarketType


GATE_MARKET_CONTRACT_VERSION = "gate-market-read-v1"


class GateMarketContractError(ValueError):
    """Base error for malformed or unsafe market evidence."""


class GateMarketKind(str, Enum):
    TRADE = "trade"
    CANDLE = "candle"
    TICKER = "ticker"
    ORDER_BOOK = "order_book"
    MARK_PRICE = "mark_price"
    INDEX_PRICE = "index_price"
    FUNDING = "funding"


class GateTradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


def _text(value: str, field: str, *, upper: bool = False) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise GateMarketContractError(f"{field} must be canonical text")
    if any(ord(ch) > 127 or ch.isspace() for ch in value):
        raise GateMarketContractError(f"{field} must be ASCII without whitespace")
    value = value.upper() if upper else value
    return value


def _decimal(value: Any, field: str, *, non_negative: bool = False, positive: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise GateMarketContractError(f"{field} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GateMarketContractError(f"{field} is not a valid decimal") from exc
    if not result.is_finite():
        raise GateMarketContractError(f"{field} must be finite")
    if non_negative and result < 0:
        raise GateMarketContractError(f"{field} must be non-negative")
    if positive and result <= 0:
        raise GateMarketContractError(f"{field} must be positive")
    return result


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise GateMarketContractError(f"{field} must be timezone-aware UTC")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateMarketContractError(f"{field} must use zero UTC offset")
    return value.astimezone(timezone.utc)


def _common(
    venue_id: str,
    market_type: AssetMarketType,
    instrument_id: str,
    occurred_at: datetime,
    observed_at: datetime,
    sequence: int,
    source_event_id: str,
    snapshot_id: str,
    rule_version: str,
    evidence_hash: str,
) -> tuple[str, AssetMarketType, str, datetime, datetime, int, str, str, str, str]:
    if _text(venue_id, "venue_id", upper=False).lower() != "gate":
        raise GateMarketContractError("only Gate evidence is accepted")
    if not isinstance(market_type, AssetMarketType) or market_type not in (AssetMarketType.SPOT, AssetMarketType.PERPETUAL):
        raise GateMarketContractError("GATE-04/05 require typed spot or perpetual market")
    instrument = _text(instrument_id, "instrument_id")
    occurred = _utc(occurred_at, "occurred_at")
    observed = _utc(observed_at, "observed_at")
    if observed < occurred:
        raise GateMarketContractError("observed_at cannot precede occurred_at")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise GateMarketContractError("sequence must be a non-negative integer")
    return (
        "gate", market_type, instrument, occurred, observed, sequence,
        _text(source_event_id, "source_event_id"), _text(snapshot_id, "snapshot_id"),
        _text(rule_version, "rule_version"), _text(evidence_hash, "evidence_hash"),
    )


@dataclass(frozen=True)
class GateTradeFact:
    market_type: AssetMarketType
    instrument_id: str
    side: GateTradeSide
    price: Decimal
    quantity: Decimal
    occurred_at: datetime
    observed_at: datetime
    sequence: int
    source_event_id: str
    snapshot_id: str
    rule_version: str
    evidence_hash: str
    venue_id: str = "gate"
    kind: GateMarketKind = GateMarketKind.TRADE

    def __post_init__(self) -> None:
        if not isinstance(self.side, GateTradeSide):
            raise GateMarketContractError("trade side must be typed")
        common = _common(self.venue_id, self.market_type, self.instrument_id, self.occurred_at, self.observed_at, self.sequence, self.source_event_id, self.snapshot_id, self.rule_version, self.evidence_hash)
        object.__setattr__(self, "venue_id", common[0]); object.__setattr__(self, "instrument_id", common[2])
        object.__setattr__(self, "occurred_at", common[3]); object.__setattr__(self, "observed_at", common[4])
        for field in ("price", "quantity"):
            object.__setattr__(self, field, _decimal(getattr(self, field), field, positive=True))


@dataclass(frozen=True)
class GateCandleFact:
    market_type: AssetMarketType
    instrument_id: str
    interval: str
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    occurred_at: datetime
    observed_at: datetime
    sequence: int
    source_event_id: str
    snapshot_id: str
    rule_version: str
    evidence_hash: str
    venue_id: str = "gate"
    kind: GateMarketKind = GateMarketKind.CANDLE

    def __post_init__(self) -> None:
        common = _common(self.venue_id, self.market_type, self.instrument_id, self.occurred_at, self.observed_at, self.sequence, self.source_event_id, self.snapshot_id, self.rule_version, self.evidence_hash)
        if _text(self.interval, "interval") not in {"1m", "5m", "15m", "1h", "4h", "1d"}:
            raise GateMarketContractError("interval is not an approved canonical value")
        opened, closed = _utc(self.open_time, "open_time"), _utc(self.close_time, "close_time")
        if closed <= opened:
            raise GateMarketContractError("close_time must be after open_time")
        prices = {name: _decimal(getattr(self, name), name, positive=True) for name in ("open_price", "high_price", "low_price", "close_price")}
        if prices["low_price"] > min(prices["open_price"], prices["close_price"]) or prices["high_price"] < max(prices["open_price"], prices["close_price"]):
            raise GateMarketContractError("candle high/low bounds are inconsistent")
        for field, value in prices.items(): object.__setattr__(self, field, value)
        object.__setattr__(self, "volume", _decimal(self.volume, "volume", non_negative=True))
        object.__setattr__(self, "open_time", opened); object.__setattr__(self, "close_time", closed)
        object.__setattr__(self, "venue_id", common[0]); object.__setattr__(self, "instrument_id", common[2])
        object.__setattr__(self, "occurred_at", common[3]); object.__setattr__(self, "observed_at", common[4])


@dataclass(frozen=True)
class GateTickerFact:
    market_type: AssetMarketType
    instrument_id: str
    bid_price: Decimal
    ask_price: Decimal
    last_price: Decimal
    occurred_at: datetime
    observed_at: datetime
    sequence: int
    source_event_id: str
    snapshot_id: str
    rule_version: str
    evidence_hash: str
    venue_id: str = "gate"
    kind: GateMarketKind = GateMarketKind.TICKER

    def __post_init__(self) -> None:
        common = _common(self.venue_id, self.market_type, self.instrument_id, self.occurred_at, self.observed_at, self.sequence, self.source_event_id, self.snapshot_id, self.rule_version, self.evidence_hash)
        bid, ask, last = (_decimal(getattr(self, name), name, positive=True) for name in ("bid_price", "ask_price", "last_price"))
        if bid > ask: raise GateMarketContractError("bid_price cannot exceed ask_price")
        object.__setattr__(self, "bid_price", bid); object.__setattr__(self, "ask_price", ask); object.__setattr__(self, "last_price", last)
        object.__setattr__(self, "venue_id", common[0]); object.__setattr__(self, "instrument_id", common[2])
        object.__setattr__(self, "occurred_at", common[3]); object.__setattr__(self, "observed_at", common[4])


@dataclass(frozen=True)
class GateOrderBookLevel:
    price: Decimal
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _decimal(self.price, "price", positive=True))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True))


@dataclass(frozen=True)
class GateOrderBookSnapshot:
    market_type: AssetMarketType
    instrument_id: str
    bids: Tuple[GateOrderBookLevel, ...]
    asks: Tuple[GateOrderBookLevel, ...]
    occurred_at: datetime
    observed_at: datetime
    sequence: int
    source_event_id: str
    snapshot_id: str
    rule_version: str
    evidence_hash: str
    # ``None`` preserves historical read-only evidence that predates the
    # streaming contract.  A snapshot without this immutable REST request
    # fact is intentionally ineligible to anchor a websocket depth stream.
    depth_limit: int | None = None
    venue_id: str = "gate"
    kind: GateMarketKind = GateMarketKind.ORDER_BOOK

    def __post_init__(self) -> None:
        common = _common(self.venue_id, self.market_type, self.instrument_id, self.occurred_at, self.observed_at, self.sequence, self.source_event_id, self.snapshot_id, self.rule_version, self.evidence_hash)
        if not self.bids or not self.asks or any(not isinstance(x, GateOrderBookLevel) for x in (*self.bids, *self.asks)):
            raise GateMarketContractError("order book levels must be typed and non-empty")
        if max(x.price for x in self.bids) >= min(x.price for x in self.asks):
            raise GateMarketContractError("order book crossed spread")
        if self.depth_limit is not None:
            if isinstance(self.depth_limit, bool) or not isinstance(self.depth_limit, int) or self.depth_limit <= 0:
                raise GateMarketContractError("depth_limit must be a positive integer when supplied")
        object.__setattr__(self, "venue_id", common[0]); object.__setattr__(self, "instrument_id", common[2])
        object.__setattr__(self, "occurred_at", common[3]); object.__setattr__(self, "observed_at", common[4])


@dataclass(frozen=True)
class GatePriceFact:
    market_type: AssetMarketType
    instrument_id: str
    price: Decimal
    occurred_at: datetime
    observed_at: datetime
    sequence: int
    source_event_id: str
    snapshot_id: str
    rule_version: str
    evidence_hash: str
    kind: GateMarketKind
    venue_id: str = "gate"

    def __post_init__(self) -> None:
        if self.kind not in (GateMarketKind.MARK_PRICE, GateMarketKind.INDEX_PRICE):
            raise GateMarketContractError("price fact kind must be mark or index")
        common = _common(self.venue_id, self.market_type, self.instrument_id, self.occurred_at, self.observed_at, self.sequence, self.source_event_id, self.snapshot_id, self.rule_version, self.evidence_hash)
        object.__setattr__(self, "price", _decimal(self.price, "price", positive=True)); object.__setattr__(self, "venue_id", common[0]); object.__setattr__(self, "instrument_id", common[2])
        object.__setattr__(self, "occurred_at", common[3]); object.__setattr__(self, "observed_at", common[4])


@dataclass(frozen=True)
class GateFundingFact:
    market_type: AssetMarketType
    instrument_id: str
    funding_rate: Decimal
    funding_interval: str
    next_funding_at: datetime
    occurred_at: datetime
    observed_at: datetime
    sequence: int
    source_event_id: str
    snapshot_id: str
    rule_version: str
    evidence_hash: str
    venue_id: str = "gate"
    kind: GateMarketKind = GateMarketKind.FUNDING

    def __post_init__(self) -> None:
        if self.market_type is not AssetMarketType.PERPETUAL:
            raise GateMarketContractError("funding is only valid for perpetual markets")
        common = _common(self.venue_id, self.market_type, self.instrument_id, self.occurred_at, self.observed_at, self.sequence, self.source_event_id, self.snapshot_id, self.rule_version, self.evidence_hash)
        object.__setattr__(self, "funding_rate", _decimal(self.funding_rate, "funding_rate"))
        object.__setattr__(self, "funding_interval", _text(self.funding_interval, "funding_interval"))
        object.__setattr__(self, "next_funding_at", _utc(self.next_funding_at, "next_funding_at"))
        if self.next_funding_at <= common[3]: raise GateMarketContractError("next_funding_at must follow occurred_at")
        object.__setattr__(self, "venue_id", common[0]); object.__setattr__(self, "instrument_id", common[2]); object.__setattr__(self, "occurred_at", common[3]); object.__setattr__(self, "observed_at", common[4])


def gate_market_identity(value: Any) -> tuple[str, str, str, int]:
    if not hasattr(value, "venue_id") or not hasattr(value, "market_type") or not hasattr(value, "instrument_id") or not hasattr(value, "sequence"):
        raise GateMarketContractError("typed market fact is required")
    return (value.venue_id, value.market_type.value, value.instrument_id, value.sequence)


def gate_market_fingerprint(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Enum): return item.value
        if isinstance(item, Decimal): return format(item.normalize(), "f")
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "__dataclass_fields__"): return normalize(asdict(item))
        if isinstance(item, dict): return {str(k): normalize(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        if isinstance(item, (tuple, list)): return [normalize(v) for v in item]
        return item
    payload = json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "GATE_MARKET_CONTRACT_VERSION", "GateCandleFact", "GateFundingFact", "GateMarketContractError", "GateMarketKind",
    "GateOrderBookLevel", "GateOrderBookSnapshot", "GatePriceFact", "GateTickerFact", "GateTradeFact", "GateTradeSide",
    "gate_market_fingerprint", "gate_market_identity",
]
