"""Market regime detection from closed-bar evidence (P0-B REGIME-01).

A regime is a stable, point-in-time classification of the market environment
derived exclusively from closed-bar data.  Strategies consume regime facts to
gate entry (e.g. mean-reversion only in RANGING, breakout only in TRENDING).

This module has NO runtime, worker, executor, exchange, or HTTP imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Tuple

from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.strategy_library_contracts import StrategyLibraryError

STRATEGY_REGIME_CONTRACT_VERSION = "strategy-regime-v1"


class MarketRegime(str, Enum):
    """Point-in-time market environment classification."""
    TRENDING_UP = "trending_up"       # ADX > threshold AND EMA slope positive
    TRENDING_DOWN = "trending_down"   # ADX > threshold AND EMA slope negative
    RANGING = "ranging"              # ADX < threshold (non-directional)
    HIGH_VOLATILITY = "high_volatility"   # ATR/price > extreme threshold
    UNKNOWN = "unknown"              # insufficient data for classification


@dataclass(frozen=True, slots=True)
class MarketRegimeFact:
    """Immutable regime evidence for one bar close.

    Strategies consume this to decide whether the current market state
    is compatible with their signal logic.
    """
    regime: MarketRegime
    bar_sequence: int
    bar_close_time: str  # ISO-8601 UTC from BacktestBar.close_time
    instrument_id: str

    # Diagnostic values (for audit, NOT for strategy decision override)
    adx_value: Decimal | None = None
    atr_pct: Decimal | None = None       # ATR / price
    ema_slope_pct: Decimal | None = None  # (EMA_now - EMA_prev) / EMA_prev
    confidence: Decimal = Decimal("0")    # 0..1, higher = more confident classification

    def is_trending(self) -> bool:
        return self.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN)

    def is_ranging(self) -> bool:
        return self.regime is MarketRegime.RANGING

    def is_high_volatility(self) -> bool:
        return self.regime is MarketRegime.HIGH_VOLATILITY


def _bars(values: Iterable[BacktestBar], minimum: int) -> Tuple[BacktestBar, ...]:
    result = tuple(values)
    if len(result) < minimum:
        raise StrategyLibraryError(f"regime detection requires at least {minimum} bars")
    if any(not isinstance(item, BacktestBar) for item in result):
        raise StrategyLibraryError("all items must be BacktestBar")
    if any(
        left.sequence >= right.sequence
        or left.close_time >= right.close_time
        for left, right in zip(result, result[1:])
    ):
        raise StrategyLibraryError("bars must be strictly ordered by sequence and close_time")
    return result


def _ema(values: Tuple[Decimal, ...], period: int) -> Decimal:
    """Deterministic EMA on Decimal values."""
    if len(values) < period:
        raise StrategyLibraryError(f"EMA needs at least {period} values")
    alpha = Decimal("2") / Decimal(str(period + 1))
    result = sum(values[:period], Decimal("0")) / Decimal(str(period))
    for v in values[period:]:
        result = (v * alpha) + (result * (Decimal("1") - alpha))
    return result


def detect_market_regime(
    values: Iterable[BacktestBar],
    *,
    adx_period: int = 14,
    adx_threshold: Decimal = Decimal("20"),
    atr_period: int = 14,
    high_vol_atr_pct: Decimal = Decimal("0.05"),  # 5% of price
    ema_trend_period: int = 50,
) -> MarketRegimeFact:
    """Classify market regime from closed-bar evidence.

    Algorithm:
      1. Compute ADX from directional movement (closed bars only).
      2. ADX < threshold → RANGING.
      3. ADX >= threshold → use EMA slope to determine TRENDING_UP/DOWN.
      4. ATR/price > high_vol_atr_pct → override to HIGH_VOLATILITY.
    """
    bars = _bars(values, max(adx_period, atr_period, ema_trend_period) + 1)
    current = bars[-1]
    closes = tuple(b.close_price for b in bars)

    # ── ADX ──────────────────────────────────────────────────
    trs, plus, minus = [], [], []
    for prev, cur in zip(bars[-adx_period - 1:-1], bars[-adx_period:]):
        up = cur.high_price - prev.high_price
        down = prev.low_price - cur.low_price
        trs.append(max(
            cur.high_price - cur.low_price,
            abs(cur.high_price - prev.close_price),
            abs(cur.low_price - prev.close_price),
        ))
        plus.append(up if up > down and up > Decimal("0") else Decimal("0"))
        minus.append(down if down > up and down > Decimal("0") else Decimal("0"))
    tr_sum = sum(trs, Decimal("0"))
    if tr_sum <= Decimal("0"):
        return MarketRegimeFact(
            MarketRegime.UNKNOWN, current.sequence,
            current.close_time.isoformat(), current.instrument_id,
        )
    plus_di = sum(plus, Decimal("0")) / tr_sum
    minus_di = sum(minus, Decimal("0")) / tr_sum
    dx = abs(plus_di - minus_di) / max(plus_di + minus_di, Decimal("1e-18"))
    adx = dx * Decimal("100")

    # ── ATR % ────────────────────────────────────────────────
    atr_ranges = []
    for prev, cur in zip(bars[-atr_period - 1:-1], bars[-atr_period:]):
        atr_ranges.append(max(
            cur.high_price - cur.low_price,
            abs(cur.high_price - prev.close_price),
            abs(cur.low_price - prev.close_price),
        ))
    atr = sum(atr_ranges, Decimal("0")) / Decimal(str(atr_period))
    atr_pct = atr / current.close_price if current.close_price > Decimal("0") else Decimal("0")

    # ── EMA trend ────────────────────────────────────────────
    if len(closes) >= ema_trend_period + 1:
        ema_now = _ema(closes, ema_trend_period)
        ema_prev = _ema(closes[:-1], ema_trend_period)
        ema_slope = (ema_now - ema_prev) / ema_prev if ema_prev > Decimal("0") else Decimal("0")
    else:
        ema_slope = Decimal("0")

    # ── Classify ─────────────────────────────────────────────
    if atr_pct > high_vol_atr_pct:
        regime = MarketRegime.HIGH_VOLATILITY
        confidence = min(Decimal("1"), atr_pct / (high_vol_atr_pct * Decimal("2")))
    elif adx < adx_threshold:
        regime = MarketRegime.RANGING
        confidence = Decimal("1") - (adx / adx_threshold)
    elif ema_slope > Decimal("0"):
        regime = MarketRegime.TRENDING_UP
        confidence = max(Decimal("0"), min(Decimal("1"), (adx - adx_threshold) / Decimal("80")))
    else:
        regime = MarketRegime.TRENDING_DOWN
        confidence = max(Decimal("0"), min(Decimal("1"), (adx - adx_threshold) / Decimal("80")))

    return MarketRegimeFact(
        regime=regime,
        bar_sequence=current.sequence,
        bar_close_time=current.close_time.isoformat(),
        instrument_id=current.instrument_id,
        adx_value=adx,
        atr_pct=atr_pct,
        ema_slope_pct=ema_slope,
        confidence=confidence,
    )


__all__ = [
    "STRATEGY_REGIME_CONTRACT_VERSION",
    "MarketRegime",
    "MarketRegimeFact",
    "detect_market_regime",
]
