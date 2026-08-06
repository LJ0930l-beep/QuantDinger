"""Pure deterministic SMC/ICT signal evidence helpers.

These functions consume a caller-owned, point-in-time candle sequence and emit
typed ``StrategySignalFact`` evidence.  They do not select an account, size a
position, call Admission, or execute a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
    EMA_ADX_TREND = "ema_adx_trend"
    DONCHIAN_BREAKOUT = "donchian_breakout"
    BOLLINGER_RSI_REVERSION = "bollinger_rsi_reversion"
    DUAL_THRUST_BREAKOUT = "dual_thrust_breakout"
    SUPERTREND_TREND = "supertrend_trend"
    BUY_AND_HOLD_ENTRY = "buy_and_hold_entry"


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


def _parameter(strategy: StrategyDefinition, name: str, default: str) -> str:
    for item in strategy.parameters:
        if item.name == name:
            return item.value
    return default


def _positive_int(value: str, field: str, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise StrategySignalContractError(f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise StrategySignalContractError(f"{field} must be a positive integer")
    return parsed


def _positive_decimal(value: str, field: str, default: Decimal) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise StrategySignalContractError(f"{field} must be a positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise StrategySignalContractError(f"{field} must be a positive decimal")
    return parsed


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    if len(values) < period:
        raise StrategySignalContractError("EMA history is incomplete")
    alpha = Decimal("2") / Decimal(period + 1)
    result = sum(values[:period], Decimal("0")) / Decimal(period)
    for value in values[period:]:
        result = (value * alpha) + (result * (Decimal("1") - alpha))
    return result


def _atr(bars: tuple[BacktestBar, ...], period: int) -> Decimal:
    if len(bars) < period + 1:
        raise StrategySignalContractError("ATR history is incomplete")
    true_ranges = []
    for previous, current in zip(bars[-period - 1:-1], bars[-period:]):
        true_ranges.append(max(
            current.high_price - current.low_price,
            abs(current.high_price - previous.close_price),
            abs(current.low_price - previous.close_price),
        ))
    return sum(true_ranges, Decimal("0")) / Decimal(period)


def detect_ema_adx_trend(
    values: Iterable[BacktestBar], *, fast_period: int = 12, slow_period: int = 26, adx_period: int = 14,
) -> StrategyStructureEvent:
    bars = _bars(values, max(slow_period, adx_period) + 1)
    if fast_period >= slow_period or fast_period < 1 or slow_period < 2 or adx_period < 1:
        raise StrategySignalContractError("EMA/ADX periods are inconsistent")
    closes = tuple(item.close_price for item in bars)
    fast, slow = _ema(closes, fast_period), _ema(closes, slow_period)
    # Deterministic ADX approximation from directional movement.  The rule is
    # deliberately explicit and uses only closed bars; it is not a venue call.
    trs, plus, minus = [], [], []
    for previous, current in zip(bars[-adx_period - 1:-1], bars[-adx_period:]):
        up = current.high_price - previous.high_price
        down = previous.low_price - current.low_price
        trs.append(max(current.high_price - current.low_price, abs(current.high_price - previous.close_price), abs(current.low_price - previous.close_price)))
        plus.append(up if up > down and up > 0 else Decimal("0"))
        minus.append(down if down > up and down > 0 else Decimal("0"))
    tr = sum(trs, Decimal("0"))
    if tr <= 0:
        return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, bars[-1].sequence, bars[-1].close_price, None, None)
    plus_di = sum(plus, Decimal("0")) / tr
    minus_di = sum(minus, Decimal("0")) / tr
    dx = abs(plus_di - minus_di) / max(plus_di + minus_di, Decimal("1e-18"))
    if dx < Decimal("0.2"):
        direction = SignalDirection.FLAT
    elif fast > slow:
        direction = SignalDirection.BUY
    elif fast < slow:
        direction = SignalDirection.SELL
    else:
        direction = SignalDirection.FLAT
    current = bars[-1]
    atr = _atr(bars, adx_period)
    stop = current.close_price - atr if direction is SignalDirection.BUY else current.close_price + atr if direction is SignalDirection.SELL else None
    target = current.close_price + atr * Decimal("2") if direction is SignalDirection.BUY else current.close_price - atr * Decimal("2") if direction is SignalDirection.SELL else None
    return StrategyStructureEvent(direction, SignalPattern.EMA_ADX_TREND if direction is not SignalDirection.FLAT else SignalPattern.NONE, current.sequence, current.close_price, stop, target)


def detect_donchian_atr(values: Iterable[BacktestBar], *, lookback: int = 20, atr_period: int = 14) -> StrategyStructureEvent:
    bars = _bars(values, max(lookback, atr_period) + 1)
    if lookback < 1 or atr_period < 1:
        raise StrategySignalContractError("Donchian/ATR periods must be positive")
    current = bars[-1]
    history = bars[-lookback - 1:-1]
    upper, lower = max(item.high_price for item in history), min(item.low_price for item in history)
    direction = SignalDirection.BUY if current.close_price > upper else SignalDirection.SELL if current.close_price < lower else SignalDirection.FLAT
    atr = _atr(bars, atr_period)
    stop = current.close_price - atr if direction is SignalDirection.BUY else current.close_price + atr if direction is SignalDirection.SELL else None
    target = current.close_price + atr * Decimal("2") if direction is SignalDirection.BUY else current.close_price - atr * Decimal("2") if direction is SignalDirection.SELL else None
    return StrategyStructureEvent(direction, SignalPattern.DONCHIAN_BREAKOUT if direction is not SignalDirection.FLAT else SignalPattern.NONE, current.sequence, current.close_price, stop, target)


def detect_bollinger_rsi(values: Iterable[BacktestBar], *, window: int = 20, deviation: Decimal = Decimal("2"), rsi_period: int = 14) -> StrategyStructureEvent:
    bars = _bars(values, max(window, rsi_period) + 1)
    if window < 2 or rsi_period < 1 or deviation <= 0:
        raise StrategySignalContractError("Bollinger/RSI periods are invalid")
    closes = tuple(item.close_price for item in bars)
    sample = closes[-window:]
    mean = sum(sample, Decimal("0")) / Decimal(window)
    variance = sum((value - mean) ** 2 for value in sample) / Decimal(window)
    # Decimal has no standard-library sqrt on older runtimes; Newton's method
    # is deterministic and bounded for positive finite variance.
    std = Decimal("0") if variance == 0 else variance.sqrt()
    upper, lower = mean + deviation * std, mean - deviation * std
    changes = [current - previous for previous, current in zip(closes[-rsi_period - 1:-1], closes[-rsi_period:])]
    gains = sum((item for item in changes if item > 0), Decimal("0")) / Decimal(rsi_period)
    losses = sum((-item for item in changes if item < 0), Decimal("0")) / Decimal(rsi_period)
    rsi = Decimal("100") if losses == 0 else Decimal("100") - (Decimal("100") / (Decimal("1") + gains / losses))
    current = bars[-1]
    direction = SignalDirection.BUY if current.close_price <= lower and rsi < Decimal("30") else SignalDirection.SELL if current.close_price >= upper and rsi > Decimal("70") else SignalDirection.FLAT
    return StrategyStructureEvent(direction, SignalPattern.BOLLINGER_RSI_REVERSION if direction is not SignalDirection.FLAT else SignalPattern.NONE, current.sequence, current.close_price, lower if direction is SignalDirection.BUY else upper if direction is SignalDirection.SELL else None, mean if direction is not SignalDirection.FLAT else None)


def detect_dual_thrust(
    values: Iterable[BacktestBar], *, lookback: int = 4,
    upper_factor: Decimal = Decimal("0.5"), lower_factor: Decimal = Decimal("0.5"),
) -> StrategyStructureEvent:
    """Emit a deterministic Dual Thrust breakout from closed bars.

    The range is ``max(HH-LC, HC-LL)`` over the preceding ``lookback`` bars;
    the current bar's open anchors both thresholds.  High/low crossings are
    used as evidence, while the threshold is retained as the entry price.
    """
    if lookback < 1 or upper_factor <= 0 or lower_factor <= 0:
        raise StrategySignalContractError("Dual Thrust parameters are invalid")
    bars = _bars(values, lookback + 1)
    current = bars[-1]
    history = bars[-lookback - 1:-1]
    highest = max(item.high_price for item in history)
    lowest = min(item.low_price for item in history)
    highest_close = max(item.close_price for item in history)
    lowest_close = min(item.close_price for item in history)
    range_value = max(highest - lowest_close, highest_close - lowest)
    buy_trigger = current.open_price + upper_factor * range_value
    sell_trigger = current.open_price - lower_factor * range_value
    if current.high_price >= buy_trigger and current.low_price <= sell_trigger:
        # Ambiguous same-bar dual crossing is not assigned a direction.
        return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, current.sequence, current.close_price, None, None)
    if current.high_price >= buy_trigger:
        risk = range_value if range_value > 0 else current.close_price * Decimal("0.01")
        return StrategyStructureEvent(SignalDirection.BUY, SignalPattern.DUAL_THRUST_BREAKOUT, current.sequence, buy_trigger, current.close_price - risk, buy_trigger + risk * Decimal("2"))
    if current.low_price <= sell_trigger:
        risk = range_value if range_value > 0 else current.close_price * Decimal("0.01")
        return StrategyStructureEvent(SignalDirection.SELL, SignalPattern.DUAL_THRUST_BREAKOUT, current.sequence, sell_trigger, current.close_price + risk, sell_trigger - risk * Decimal("2"))
    return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, current.sequence, current.close_price, None, None)


def detect_supertrend_ema_adx(
    values: Iterable[BacktestBar],
    *,
    atr_period: int = 10,
    atr_multiplier: int = 3,
    ema_fast: int = 12,
    ema_slow: int = 50,
    adx_period: int = 14,
    adx_min: int = 20,
) -> StrategyStructureEvent:
    """Deterministic SuperTrend + EMA + ADX trend signal from closed bars.

    SuperTrend is computed from ATR-based bands that flip direction.
    EMA crossover confirms trend direction. ADX gates entry strength.
    """
    min_len = max(atr_period, ema_slow, adx_period) + 1
    bars = _bars(values, min_len)
    if atr_period < 1 or atr_multiplier < 1 or ema_fast < 1 or ema_slow < 1 or adx_period < 1 or adx_min < 1:
        raise StrategySignalContractError("SuperTrend/EMA/ADX periods must be positive")
    if ema_fast >= ema_slow:
        raise StrategySignalContractError("fast EMA must be shorter than slow EMA")

    closes = tuple(item.close_price for item in bars)
    highs = tuple(item.high_price for item in bars)
    lows = tuple(item.low_price for item in bars)

    # ── ATR ──────────────────────────────────────────────────
    atr = _atr(bars, atr_period)

    # ── SuperTrend band flip detection ───────────────────────
    src = tuple((h + l) / Decimal("2") for h, l in zip(highs, lows))
    upper_bands = []
    lower_bands = []
    trends = []
    mult = Decimal(str(atr_multiplier))
    for i in range(atr_period, len(closes)):
        mid = src[i]
        if i == atr_period:
            upper_bands.append(mid + mult * atr)
            lower_bands.append(mid - mult * atr)
            trends.append(True)
        else:
            prev_close = closes[i - 1]
            prev_upper = upper_bands[-1]
            prev_lower = lower_bands[-1]
            nu = mid + mult * atr
            nl = mid - mult * atr
            if prev_close > prev_upper:
                au = nu
            else:
                au = min(nu, prev_upper)
            if prev_close < prev_lower:
                al = nl
            else:
                al = max(nl, prev_lower)
            upper_bands.append(au)
            lower_bands.append(al)
            if prev_close > prev_lower:
                trends.append(True)
            elif prev_close < prev_upper:
                trends.append(False)
            else:
                trends.append(trends[-1])

    st_bull = trends[-1]
    st_bull_prev = trends[-2] if len(trends) >= 2 else st_bull
    flips_bull = st_bull and not st_bull_prev
    flips_bear = not st_bull and st_bull_prev

    # ── EMA ──────────────────────────────────────────────────
    fast = _ema(closes, ema_fast)
    slow = _ema(closes, ema_slow)
    ema_bull = fast > slow
    ema_bear = fast < slow

    # ── ADX ──────────────────────────────────────────────────
    trs, pd, nd = [], [], []
    for prev, cur in zip(bars[-adx_period - 1:-1], bars[-adx_period:]):
        up = cur.high_price - prev.high_price
        down = prev.low_price - cur.low_price
        trs.append(max(
            cur.high_price - cur.low_price,
            abs(cur.high_price - prev.close_price),
            abs(cur.low_price - prev.close_price),
        ))
        pd.append(up if up > down and up > Decimal("0") else Decimal("0"))
        nd.append(down if down > up and down > Decimal("0") else Decimal("0"))
    tr_sum = sum(trs, Decimal("0"))
    if tr_sum <= Decimal("0"):
        return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, bars[-1].sequence, bars[-1].close_price, None, None)
    pdi = sum(pd, Decimal("0")) / tr_sum
    ndi = sum(nd, Decimal("0")) / tr_sum
    dx = abs(pdi - ndi) / max(pdi + ndi, Decimal("1e-18"))
    adx_val = dx * Decimal("100")
    strong = adx_val >= Decimal(str(adx_min))

    current = bars[-1]

    # ── Signal logic ─────────────────────────────────────────
    # LONG: SuperTrend flips bull + EMA bull + ADX strong
    if flips_bull and ema_bull and strong:
        stop = current.close_price - atr * Decimal("2")
        target = current.close_price + atr * Decimal("3")
        return StrategyStructureEvent(
            SignalDirection.BUY, SignalPattern.SUPERTREND_TREND,
            current.sequence, current.close_price, stop, target,
        )
    # SHORT: SuperTrend flips bear + EMA bear + ADX strong
    if flips_bear and ema_bear and strong:
        stop = current.close_price + atr * Decimal("2")
        target = current.close_price - atr * Decimal("3")
        return StrategyStructureEvent(
            SignalDirection.SELL, SignalPattern.SUPERTREND_TREND,
            current.sequence, current.close_price, stop, target,
        )
    return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, current.sequence, current.close_price, None, None)


def detect_buy_and_hold(values: Iterable[BacktestBar]) -> StrategyStructureEvent:
    bars = _bars(values, 1)
    current = bars[-1]
    # The backtest runner supplies an expanding closed-bar window.  Emit one
    # deterministic entry at the first evaluation and remain flat thereafter.
    if len(bars) != 4:
        return StrategyStructureEvent(SignalDirection.FLAT, SignalPattern.NONE, current.sequence, current.close_price, None, None)
    return StrategyStructureEvent(SignalDirection.BUY, SignalPattern.BUY_AND_HOLD_ENTRY, current.sequence, current.close_price, None, None)


def build_strategy_signal(strategy: StrategyDefinition, values: Iterable[BacktestBar], *, signal_id: str, data_snapshot_id: str) -> StrategySignalFact:
    if not isinstance(strategy, StrategyDefinition):
        raise StrategySignalContractError("strategy must be typed")
    bars = tuple(values)
    if strategy.family is StrategyFamily.BUY_AND_HOLD:
        event = detect_buy_and_hold(bars)
    elif strategy.family is StrategyFamily.EMA_ADX_TREND:
        event = detect_ema_adx_trend(
            bars,
            fast_period=_positive_int(_parameter(strategy, "fast_period", "12"), "fast_period", 12),
            slow_period=_positive_int(_parameter(strategy, "slow_period", "26"), "slow_period", 26),
            adx_period=_positive_int(_parameter(strategy, "adx_period", "14"), "adx_period", 14),
        )
    elif strategy.family is StrategyFamily.DONCHIAN_ATR:
        event = detect_donchian_atr(
            bars,
            lookback=_positive_int(_parameter(strategy, "lookback", "20"), "lookback", 20),
            atr_period=_positive_int(_parameter(strategy, "atr_period", "14"), "atr_period", 14),
        )
    elif strategy.family is StrategyFamily.BOLLINGER_RSI:
        event = detect_bollinger_rsi(
            bars,
            window=_positive_int(_parameter(strategy, "window", "20"), "window", 20),
            deviation=_positive_decimal(_parameter(strategy, "deviation", "2"), "deviation", Decimal("2")),
            rsi_period=_positive_int(_parameter(strategy, "rsi_period", "14"), "rsi_period", 14),
        )
    elif strategy.family is StrategyFamily.DUAL_THRUST:
        event = detect_dual_thrust(
            bars,
            lookback=_positive_int(_parameter(strategy, "lookback", "4"), "lookback", 4),
            upper_factor=_positive_decimal(_parameter(strategy, "upper_factor", "0.5"), "upper_factor", Decimal("0.5")),
            lower_factor=_positive_decimal(_parameter(strategy, "lower_factor", "0.5"), "lower_factor", Decimal("0.5")),
        )
    elif strategy.family is StrategyFamily.SMC:
        event = detect_liquidity_sweep(bars)
    elif strategy.family is StrategyFamily.ICT:
        event = detect_displacement(bars)
    elif strategy.family is StrategyFamily.SUPERTREND_EMA_ADX:
        event = detect_supertrend_ema_adx(
            bars,
            atr_period=_positive_int(_parameter(strategy, "atr_period", "10"), "atr_period", 10),
            atr_multiplier=_positive_int(_parameter(strategy, "atr_multiplier", "3"), "atr_multiplier", 3),
            ema_fast=_positive_int(_parameter(strategy, "ema_fast", "12"), "ema_fast", 12),
            ema_slow=_positive_int(_parameter(strategy, "ema_slow", "50"), "ema_slow", 50),
            adx_period=_positive_int(_parameter(strategy, "adx_period", "14"), "adx_period", 14),
            adx_min=_positive_int(_parameter(strategy, "adx_min", "20"), "adx_min", 20),
        )
    else:
        raise StrategySignalContractError("strategy family is not supported")
    bar = bars[-1]
    return StrategySignalFact(signal_id, strategy, bar.instrument_id, event.direction, Decimal("1") if event.direction is not SignalDirection.FLAT else Decimal("0"), bar.close_time, event.source_sequence, data_snapshot_id, event.reference_price if event.direction is not SignalDirection.FLAT else None, event.stop_price, event.target_price)


__all__ = [
    "STRATEGY_SIGNAL_CONTRACT_VERSION", "SignalPattern", "StrategySignalContractError", "StrategyStructureEvent",
    "build_strategy_signal", "detect_displacement", "detect_liquidity_sweep", "detect_ema_adx_trend",
    "detect_donchian_atr", "detect_bollinger_rsi", "detect_dual_thrust", "detect_supertrend_ema_adx", "detect_buy_and_hold",
]
