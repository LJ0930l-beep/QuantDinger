"""Deterministic, read-only backtest contracts (BT-01).

This module describes replayable decisions; it is not an execution engine and
never contacts an exchange or writes a position/order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Tuple


BACKTEST_CONTRACT_VERSION = "backtest-deterministic-v1"


class BacktestContractError(ValueError):
    """Base error for invalid or ambiguous backtest facts."""


class BacktestSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class BacktestExecutionKind(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class BacktestDecision(str, Enum):
    EXECUTED = "executed"
    NOT_EXECUTED = "not_executed"
    INVALID = "invalid"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) > 127 or c.isspace() for c in value):
        raise BacktestContractError(f"{field} must be canonical ASCII text")
    return value


def _reason(value: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) < 32 for c in value):
        raise BacktestContractError("reason must be canonical text")
    return value


def _decimal(value: Any, field: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise BacktestContractError(f"{field} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BacktestContractError(f"{field} is not a valid decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (non_negative and result < 0):
        raise BacktestContractError(f"{field} has invalid numeric bounds")
    return result


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise BacktestContractError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class BacktestRunFacts:
    run_id: str
    dataset_snapshot_id: str
    instrument_rule_version: str
    fee_policy_version: str
    slippage_policy_version: str
    initial_cash: Decimal
    valuation_ccy: str
    clock_start: datetime
    clock_end: datetime

    def __post_init__(self) -> None:
        for field in ("run_id", "dataset_snapshot_id", "instrument_rule_version", "fee_policy_version", "slippage_policy_version", "valuation_ccy"):
            _text(getattr(self, field), field)
        object.__setattr__(self, "initial_cash", _decimal(self.initial_cash, "initial_cash", non_negative=True))
        start, end = _utc(self.clock_start, "clock_start"), _utc(self.clock_end, "clock_end")
        if end <= start: raise BacktestContractError("clock_end must follow clock_start")
        object.__setattr__(self, "clock_start", start); object.__setattr__(self, "clock_end", end)


@dataclass(frozen=True)
class BacktestBar:
    instrument_id: str
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal
    sequence: int
    snapshot_id: str

    def __post_init__(self) -> None:
        _text(self.instrument_id, "instrument_id"); _text(self.snapshot_id, "snapshot_id")
        opened, closed = _utc(self.open_time, "open_time"), _utc(self.close_time, "close_time")
        if closed <= opened: raise BacktestContractError("bar close must follow open")
        prices = {name: _decimal(getattr(self, name), name, positive=True) for name in ("open_price", "high_price", "low_price", "close_price")}
        if prices["low_price"] > min(prices["open_price"], prices["close_price"]) or prices["high_price"] < max(prices["open_price"], prices["close_price"]):
            raise BacktestContractError("bar high/low bounds are inconsistent")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0: raise BacktestContractError("sequence must be non-negative")
        for name, value in prices.items(): object.__setattr__(self, name, value)
        object.__setattr__(self, "volume", _decimal(self.volume, "volume", non_negative=True)); object.__setattr__(self, "open_time", opened); object.__setattr__(self, "close_time", closed)


@dataclass(frozen=True)
class BacktestOrderIntent:
    order_id: str
    instrument_id: str
    side: BacktestSide
    execution_kind: BacktestExecutionKind
    quantity: Decimal
    submitted_at: datetime
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        _text(self.order_id, "order_id"); _text(self.instrument_id, "instrument_id")
        if not isinstance(self.side, BacktestSide) or not isinstance(self.execution_kind, BacktestExecutionKind): raise BacktestContractError("typed order fields are required")
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", positive=True)); object.__setattr__(self, "submitted_at", _utc(self.submitted_at, "submitted_at"))
        if self.execution_kind is BacktestExecutionKind.LIMIT:
            if self.limit_price is None: raise BacktestContractError("limit order requires limit_price")
            object.__setattr__(self, "limit_price", _decimal(self.limit_price, "limit_price", positive=True))
        elif self.limit_price is not None: raise BacktestContractError("market order cannot carry limit_price")


@dataclass(frozen=True)
class BacktestExecutionDecision:
    order_id: str
    decision: BacktestDecision
    fill_time: datetime | None
    fill_price: Decimal | None
    reason: str

    def __post_init__(self) -> None:
        _text(self.order_id, "order_id"); _reason(self.reason)
        if self.fill_time is not None: object.__setattr__(self, "fill_time", _utc(self.fill_time, "fill_time"))
        if self.fill_price is not None: object.__setattr__(self, "fill_price", _decimal(self.fill_price, "fill_price", positive=True))
        if self.decision is BacktestDecision.EXECUTED and (self.fill_time is None or self.fill_price is None): raise BacktestContractError("executed decision requires fill facts")
        if self.decision is not BacktestDecision.EXECUTED and (self.fill_time is not None or self.fill_price is not None): raise BacktestContractError("non-executed decision cannot carry fill facts")


def next_open_execution(order: BacktestOrderIntent, bar: BacktestBar) -> BacktestExecutionDecision:
    """Evaluate an order only at a later bar open; same-bar future leakage fails closed."""
    if order.instrument_id != bar.instrument_id or bar.open_time <= order.submitted_at:
        return BacktestExecutionDecision(order.order_id, BacktestDecision.INVALID, None, None, "bar is not a later eligible open")
    if order.execution_kind is BacktestExecutionKind.MARKET:
        return BacktestExecutionDecision(order.order_id, BacktestDecision.EXECUTED, bar.open_time, bar.open_price, "next_open_market")
    limit = order.limit_price
    assert limit is not None
    crossed = (order.side is BacktestSide.BUY and bar.low_price <= limit) or (order.side is BacktestSide.SELL and bar.high_price >= limit)
    if not crossed:
        return BacktestExecutionDecision(order.order_id, BacktestDecision.NOT_EXECUTED, None, None, "limit_not_reached")
    return BacktestExecutionDecision(order.order_id, BacktestDecision.EXECUTED, bar.open_time, limit, "next_open_limit")


def backtest_fingerprint(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Enum): return item.value
        if isinstance(item, Decimal): return format(item.normalize(), "f")
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "__dataclass_fields__"): return normalize(asdict(item))
        if isinstance(item, dict): return {str(k): normalize(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        if isinstance(item, (tuple, list)): return [normalize(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


__all__ = ["BACKTEST_CONTRACT_VERSION", "BacktestBar", "BacktestContractError", "BacktestDecision", "BacktestExecutionDecision", "BacktestExecutionKind", "BacktestOrderIntent", "BacktestRunFacts", "BacktestSide", "backtest_fingerprint", "next_open_execution"]
