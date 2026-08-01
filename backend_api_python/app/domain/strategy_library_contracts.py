"""Deterministic strategy definition and signal facts (STRAT-01).

Strategies emit typed signal evidence only.  Candidate-to-Admission conversion
is handled by the existing Strategy V2 boundary; this module has no runtime,
worker, executor, exchange, or live authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Tuple


STRATEGY_LIBRARY_CONTRACT_VERSION = "strategy-library-v1"


class StrategyLibraryError(ValueError):
    """Invalid or incomplete strategy evidence."""


class StrategyFamily(str, Enum):
    BUY_AND_HOLD = "buy_and_hold"
    EMA_ADX_TREND = "ema_adx_trend"
    DONCHIAN_ATR = "donchian_atr"
    BOLLINGER_RSI = "bollinger_rsi"
    SMC = "smc"
    ICT = "ict"


class SignalDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"
    FLAT = "flat"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or any(ord(c) > 127 or c.isspace() for c in value):
        raise StrategyLibraryError(f"{field} must be canonical ASCII text")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise StrategyLibraryError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


def _decimal(value: Any, field: str, *, positive: bool = False, ratio: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise StrategyLibraryError(f"{field} rejects float/bool input")
    try: result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc: raise StrategyLibraryError(f"{field} is not a decimal") from exc
    if not result.is_finite() or (positive and result <= 0) or (ratio and not (Decimal("0") <= result <= Decimal("1"))):
        raise StrategyLibraryError(f"{field} has invalid numeric bounds")
    return result


@dataclass(frozen=True)
class StrategyParameterFact:
    name: str
    value: str

    def __post_init__(self) -> None:
        _text(self.name, "parameter name"); _text(self.value, "parameter value")


@dataclass(frozen=True)
class StrategyDefinition:
    strategy_id: str
    version: str
    family: StrategyFamily
    parameter_schema_fingerprint: str
    data_dependency_snapshot: str
    parameters: Tuple[StrategyParameterFact, ...]

    def __post_init__(self) -> None:
        for field in ("strategy_id", "version", "parameter_schema_fingerprint", "data_dependency_snapshot"): _text(getattr(self, field), field)
        if not isinstance(self.family, StrategyFamily): raise StrategyLibraryError("family must be typed")
        if not self.parameters or any(not isinstance(p, StrategyParameterFact) for p in self.parameters): raise StrategyLibraryError("typed parameter facts are required")
        if len({p.name for p in self.parameters}) != len(self.parameters): raise StrategyLibraryError("parameter names must be unique")


@dataclass(frozen=True)
class StrategySignalFact:
    signal_id: str
    strategy: StrategyDefinition
    instrument_id: str
    direction: SignalDirection
    confidence: Decimal
    occurred_at: datetime
    source_sequence: int
    data_snapshot_id: str
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    target_price: Decimal | None = None

    def __post_init__(self) -> None:
        _text(self.signal_id, "signal_id"); _text(self.instrument_id, "instrument_id"); _text(self.data_snapshot_id, "data_snapshot_id")
        if not isinstance(self.strategy, StrategyDefinition) or not isinstance(self.direction, SignalDirection): raise StrategyLibraryError("typed strategy and direction are required")
        object.__setattr__(self, "confidence", _decimal(self.confidence, "confidence", ratio=True)); object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))
        if not isinstance(self.source_sequence, int) or isinstance(self.source_sequence, bool) or self.source_sequence < 0: raise StrategyLibraryError("source_sequence must be non-negative")
        for field in ("entry_price", "stop_price", "target_price"):
            value = getattr(self, field)
            if value is not None: object.__setattr__(self, field, _decimal(value, field, positive=True))
        if self.direction is SignalDirection.FLAT and any(getattr(self, f) is not None for f in ("entry_price", "stop_price", "target_price")):
            raise StrategyLibraryError("flat signal cannot contain trade prices")


def strategy_fingerprint(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, Enum): return item.value
        if isinstance(item, Decimal): return format(item.normalize(), "f")
        if isinstance(item, datetime): return _utc(item, "timestamp").isoformat()
        if hasattr(item, "__dataclass_fields__"): return normalize(asdict(item))
        if isinstance(item, dict): return {str(k): normalize(v) for k, v in sorted(item.items(), key=lambda x: str(x[0]))}
        if isinstance(item, (tuple, list)): return [normalize(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


__all__ = ["STRATEGY_LIBRARY_CONTRACT_VERSION", "SignalDirection", "StrategyDefinition", "StrategyFamily", "StrategyLibraryError", "StrategyParameterFact", "StrategySignalFact", "strategy_fingerprint"]
