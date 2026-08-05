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
    DUAL_THRUST = "dual_thrust"
    SMC = "smc"
    ICT = "ict"


_SUPPORTED_TIMEFRAMES = frozenset({"1m", "5m", "15m", "30m", "1h", "4h", "1d"})
_MARKET_TYPE_GROUPS = {
    "spot": "crypto",
    "perpetual": "crypto",
    "swap": "crypto",
    "future": "crypto",
    "futures": "crypto",
    "delivery": "crypto",
    "options": "crypto",
    "stocks": "us_stock",
    "etf": "us_stock",
}


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
    supported_timeframes: Tuple[str, ...] = ("1d",)
    supported_market_types: Tuple[str, ...] = ("crypto",)

    def __post_init__(self) -> None:
        for field in ("strategy_id", "version", "parameter_schema_fingerprint", "data_dependency_snapshot"): _text(getattr(self, field), field)
        if not isinstance(self.family, StrategyFamily): raise StrategyLibraryError("family must be typed")
        if not self.parameters or any(not isinstance(p, StrategyParameterFact) for p in self.parameters): raise StrategyLibraryError("typed parameter facts are required")
        if len({p.name for p in self.parameters}) != len(self.parameters): raise StrategyLibraryError("parameter names must be unique")
        if (
            not isinstance(self.supported_timeframes, tuple)
            or not self.supported_timeframes
            or any(item not in _SUPPORTED_TIMEFRAMES for item in self.supported_timeframes)
            or len(set(self.supported_timeframes)) != len(self.supported_timeframes)
        ):
            raise StrategyLibraryError("supported_timeframes must contain unique supported values")
        if (
            not isinstance(self.supported_market_types, tuple)
            or not self.supported_market_types
            or any(not isinstance(item, str) or not item or item != item.strip() or item != item.lower() or any(ord(c) > 127 or c.isspace() for c in item) for item in self.supported_market_types)
            or len(set(self.supported_market_types)) != len(self.supported_market_types)
        ):
            raise StrategyLibraryError("supported_market_types must contain unique canonical values")


def coerce_strategy_definition(value: object) -> StrategyDefinition:
    """Rebind an equivalent immutable strategy fact to this module's classes.

    Test/plugin loaders can temporarily load the domain module under an
    isolated name.  That must not make an otherwise complete strategy
    definition fail merely because Python class identity differs.  The
    boundary remains fail-closed: only the exact typed fact name and all
    immutable fields are accepted, then every field is reconstructed through
    the canonical validators above.
    """

    if isinstance(value, StrategyDefinition):
        return value
    if type(value).__name__ != "StrategyDefinition":
        raise StrategyLibraryError("strategy must be typed")
    required = (
        "strategy_id", "version", "family", "parameter_schema_fingerprint",
        "data_dependency_snapshot", "parameters", "supported_timeframes",
        "supported_market_types",
    )
    if any(not hasattr(value, field) for field in required):
        raise StrategyLibraryError("strategy definition is incomplete")
    try:
        family = value.family
        if not isinstance(family, StrategyFamily):
            family = StrategyFamily(getattr(family, "value", family))
        parameters = tuple(
            parameter if isinstance(parameter, StrategyParameterFact)
            else StrategyParameterFact(parameter.name, parameter.value)
            for parameter in value.parameters
        )
        return StrategyDefinition(
            value.strategy_id,
            value.version,
            family,
            value.parameter_schema_fingerprint,
            value.data_dependency_snapshot,
            parameters,
            tuple(value.supported_timeframes),
            tuple(value.supported_market_types),
        )
    except (AttributeError, TypeError, ValueError, StrategyLibraryError) as exc:
        raise StrategyLibraryError("strategy definition is invalid") from exc


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


def coerce_strategy_signal(value: object) -> StrategySignalFact:
    """Rebind an equivalent immutable signal fact to this module's classes.

    Isolated research/test loaders can give an otherwise complete dataclass a
    different Python class identity.  Accept only the exact signal type name
    and complete immutable field set, then reconstruct through canonical
    validators so the boundary remains fail closed.
    """
    if isinstance(value, StrategySignalFact):
        return value
    if type(value).__name__ != "StrategySignalFact":
        raise StrategyLibraryError("signal must be typed")
    required = (
        "signal_id", "strategy", "instrument_id", "direction", "confidence",
        "occurred_at", "source_sequence", "data_snapshot_id", "entry_price",
        "stop_price", "target_price",
    )
    if any(not hasattr(value, field) for field in required):
        raise StrategyLibraryError("strategy signal is incomplete")
    try:
        direction = value.direction
        if not isinstance(direction, SignalDirection):
            direction = SignalDirection(getattr(direction, "value", direction))
        return StrategySignalFact(
            value.signal_id,
            coerce_strategy_definition(value.strategy),
            value.instrument_id,
            direction,
            value.confidence,
            value.occurred_at,
            value.source_sequence,
            value.data_snapshot_id,
            value.entry_price,
            value.stop_price,
            value.target_price,
        )
    except (AttributeError, TypeError, ValueError, StrategyLibraryError) as exc:
        raise StrategyLibraryError("strategy signal is invalid") from exc


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


def strategy_supports_scope(strategy: StrategyDefinition, *, timeframe: str, market_type: str) -> bool:
    """Return whether a typed strategy is approved for one research scope.

    Scope validation is deliberately opt-in at the service boundary: callers
    must provide the observed timeframe and market type instead of letting a
    runtime infer them from a symbol or venue.  Invalid scope input fails
    closed rather than returning a permissive result.
    """

    if not isinstance(strategy, StrategyDefinition):
        raise StrategyLibraryError("strategy must be typed")
    _text(timeframe, "timeframe")
    if timeframe not in _SUPPORTED_TIMEFRAMES:
        raise StrategyLibraryError("timeframe is not supported")
    market_type = _text(market_type, "market_type").lower()
    market_group = _MARKET_TYPE_GROUPS.get(market_type, market_type)
    return timeframe in strategy.supported_timeframes and market_group in strategy.supported_market_types


__all__ = ["STRATEGY_LIBRARY_CONTRACT_VERSION", "SignalDirection", "StrategyDefinition", "StrategyFamily", "StrategyLibraryError", "StrategyParameterFact", "StrategySignalFact", "coerce_strategy_definition", "coerce_strategy_signal", "strategy_fingerprint", "strategy_supports_scope"]
