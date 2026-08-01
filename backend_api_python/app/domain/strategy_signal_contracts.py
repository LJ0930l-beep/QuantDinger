"""Pure deterministic SMC/ICT signal evidence helpers.

These functions consume a caller-owned, point-in-time candle sequence and emit
typed ``StrategySignalFact`` evidence.  They do not select an account, size a
position, call Admission, or execute a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Tuple

from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.strategy_library_contracts import SignalDirection, StrategyDefinition, StrategyFamily, StrategyLibraryError, StrategySignalFact


STRATEGY_SIGNAL_CONTRACT_VERSION = "strategy-signal-v1"


class StrategySignalContractError(StrategyLibraryError):
    """Invalid or insufficient candle evidence for a deterministic signal."""


class SignalPattern(str, Enum):
    NONE = "none"
    LIQUIDITY_SWEEP = "liquidity_sweep"
    DISPLACEMENT = "displacement"


@dataclass(frozen=True, slots=True)
class StrategyStructureEvent:
    direction: SignalDirection
    pattern: SignalPattern
    source_sequence: int
    reference_price: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None


def _bars(values: Iterable[BacktestBar], minimum: int) -> Tuple[BacktestBar, ...]:
    result = tuple(values)
    if len(result) < minimum or any(not isinstance(item, BacktestBar) for item in result):
        raise StrategySignalContractError(f"at least {minimum} typed bars are required")
    if any(left.instrument_id != right.instrument_id for left, right in zip(result, result[1:])):
        raise StrategySignalContractError("bars must use one instrument")
    if any(left.sequence >= right.sequence or left.close_time >= right.close_time for left, right in zip(result, result[1:])):
        raise StrategySignalContractError("bars must be strictly ordered")
    return result


def detect_liquidity_sweep(values: Iterable[BacktestBar], *, lookback: int = 3) -> StrategyStructureEvent:
    bars = _bars(values, lookback + 1)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
        raise StrategySignalContractError("lookback must be a positive integer")
    current = bars[-1]
    history = bars[-lookback - 1:-1]
    prior_high = max(item.high_price for item in history)
    prior_low = min(item.low_price for item in history)
    if current.high_price > prior_high and current.close_price < prior_high:
        risk = current.high_price - current.close_price
        return StrategyStructureEvent(SignalDirection.SELL, SignalPattern.LIQUIDITY_SWEEP, current.sequence, current.close_price, current.high_price, current.close_price - risk * Decimal("2"))
    if current.low_price < prior_low and current.close_price > prior_low:
        risk = current.close_price - current.low_price
        return StrategyStructureEvent(SignalDirection.BUY, SignalPattern.LIQUIDITY_SWEEP, current.sequence, current.close_price, current.low_price, current.close_price + risk * Decimal("2"))
    return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, current.sequence, current.close_price, None, None)


def detect_displacement(values: Iterable[BacktestBar], *, lookback: int = 3, multiplier: Decimal = Decimal("1.5")) -> StrategyStructureEvent:
    bars = _bars(values, lookback + 1)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1 or isinstance(multiplier, (float, bool)) or multiplier <= 0:
        raise StrategySignalContractError("displacement parameters are invalid")
    current = bars[-1]
    history = bars[-lookback - 1:-1]
    average_body = sum((abs(item.close_price - item.open_price) for item in history), Decimal(0)) / Decimal(len(history))
    body = abs(current.close_price - current.open_price)
    if body < average_body * multiplier or body == 0:
        return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, current.sequence, current.close_price, None, None)
    direction = SignalDirection.BUY if current.close_price > current.open_price else SignalDirection.SELL
    risk = body
    stop = current.open_price - risk if direction is SignalDirection.BUY else current.open_price + risk
    target = current.close_price + risk * Decimal("2") if direction is SignalDirection.BUY else current.close_price - risk * Decimal("2")
    return StrategyStructureEvent(direction, SignalPattern.DISPLACEMENT, current.sequence, current.close_price, stop, target)


def build_strategy_signal(strategy: StrategyDefinition, values: Iterable[BacktestBar], *, signal_id: str, data_snapshot_id: str) -> StrategySignalFact:
    if not isinstance(strategy, StrategyDefinition):
        raise StrategySignalContractError("strategy must be typed")
    bars = tuple(values)
    if strategy.family is StrategyFamily.SMC:
        event = detect_liquidity_sweep(bars)
    elif strategy.family is StrategyFamily.ICT:
        event = detect_displacement(bars)
    else:
        raise StrategySignalContractError("only SMC and ICT rule contracts are supported")
    bar = bars[-1]
    return StrategySignalFact(signal_id, strategy, bar.instrument_id, event.direction, Decimal("1") if event.direction is not SignalDirection.FLAT else Decimal("0"), bar.close_time, event.source_sequence, data_snapshot_id, event.reference_price if event.direction is not SignalDirection.FLAT else None, event.stop_price, event.target_price)


__all__ = ["STRATEGY_SIGNAL_CONTRACT_VERSION", "SignalPattern", "StrategySignalContractError", "StrategyStructureEvent", "build_strategy_signal", "detect_displacement", "detect_liquidity_sweep"]
