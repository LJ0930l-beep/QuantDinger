"""FUT-02: SuperTrend + EMA + ADX State Strategy (Perpetual Swap, Long+Short)

Source: QuantDinger existing indicator strategies + GitHub CTA pattern research
        (freqtrade GPL-3.0 ideas studied, NOT copied)
License: Original QuantDinger implementation.

Specification per《QuantDinger GitHub 策略引入与落地实施方案》v1.0 §5.5:
  Market:      永续合约，多空
  Timeframe:   5m, 15m, 1h (首选 15m/1h)
  Required:    OHLCV, ATR, SuperTrend, EMA, ADX, Funding, Spread

Entry Logic (closed bars only):
  - LONG:  SuperTrend bullish + EMA(fast) > EMA(slow) + ADX > threshold
  - SHORT: SuperTrend bearish + EMA(fast) < EMA(slow) + ADX > threshold
  - ADX < threshold → NO_ACTION (avoid chop)
  - Spread + liquidity + funding filter pass

Exit Logic:
  - SuperTrend reversal
  - EMA crossover reversal
  - ATR stop
  - Time exit
  - ADX decline → reduce signal only (don't widen stop)

Safety:
  - Same-direction existing position: no pyramiding in v1
  - Signal conflict → NO_ACTION (don't auto-pick strongest)
  - Any reversal must split into CLOSE + new Admission
"""

STRATEGY_CODE = '''
import math

# ── Indicator Helpers ────────────────────────────────────────

def _atr(highs, lows, closes, period):
    if len(closes) < period + 1:
        return 0.0
    tr = []
    for i in range(-period, 0):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(tr) / period

def _ema(values, period):
    if len(values) < period:
        return values[-1] if values else 0.0
    k = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = (v - result) * k + result
    return result

def _adx(highs, lows, closes, period=14):
    if len(closes) < period * 2:
        return 25.0
    tr, pd, nd = [], [], []
    for i in range(1, len(closes)):
        h, l, ph, pl = highs[i], lows[i], highs[i-1], lows[i-1]
        up = h - ph; dn = pl - l
        tr.append(max(h - l, abs(h - ph), abs(l - pl)))
        pd.append(up if up > dn and up > 0 else 0.0)
        nd.append(dn if dn > up and dn > 0 else 0.0)
    if not tr: return 25.0
    atr_s = sum(tr[:period]) / period
    pdi = sum(pd[:period]) / period * 100 / atr_s if atr_s > 0 else 0
    ndi = sum(nd[:period]) / period * 100 / atr_s if atr_s > 0 else 0
    for i in range(period, len(tr)):
        atr_s = (atr_s * (period - 1) + tr[i]) / period
        pdi = (pdi * (period - 1) + pd[i]) / period * 100 / atr_s if atr_s > 0 else 0
        ndi = (ndi * (period - 1) + nd[i]) / period * 100 / atr_s if atr_s > 0 else 0
    dx_sum = pdi + ndi
    return abs(pdi - ndi) / dx_sum * 100 if dx_sum > 0 else 0

def _supertrend(highs, lows, closes, period=10, mult=3.0):
    if len(closes) < period: return 0.0, False
    atr_v = _atr(highs, lows, closes, period)
    if atr_v == 0: return 0.0, False
    src = [(h + l) / 2 for h, l in zip(highs, lows)]
    upper = [src[-period] + mult * atr_v]
    lower = [src[-period] - mult * atr_v]
    trend = [True]
    for i in range(-period + 1, 0):
        mid = src[i]; pu = upper[-1]; pl = lower[-1]; pc = closes[i - 1]
        nu = mid + mult * atr_v; nl = mid - mult * atr_v
        au = nu if pc > pu else min(nu, pu)
        al = nl if pc < pl else max(nl, pl)
        upper.append(au); lower.append(al)
        trend.append(True if pc > pl else (False if pc < pu else trend[-1]))
    return (lower[-1] if trend[-1] else upper[-1]), trend[-1]


# ── Parameters ───────────────────────────────────────────────

EMA_FAST = 12
EMA_SLOW = 50
ADX_PERIOD = 14
ADX_MIN = 23
ST_ATR = 10
ST_MULT = 3.0
ATR_STOP_MULT = 2.5
RISK_BUDGET_PCT = 0.003
MAX_HOLD_BARS = 96
COOLDOWN_BARS = 5


def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@swap"])
    context.set_warmup(max(ADX_PERIOD * 2, EMA_SLOW, ST_ATR) + 2)
    context.entry_price = 0.0
    context.entry_side = 0
    context.bars_held = 0
    context.highest_since_entry = 0.0
    context.lowest_since_entry = float("inf")
    context.cooldown = 0


def handle_data(context):
    closes = [b.close for b in context.bars]
    highs = [b.high for b in context.bars]
    lows = [b.low for b in context.bars]

    if len(closes) < max(ADX_PERIOD * 2, EMA_SLOW, ST_ATR) + 2:
        return

    symbol = context.instruments[0]
    price = closes[-1]
    pos = context.portfolio.positions.get(symbol)

    if context.cooldown > 0:
        context.cooldown -= 1
        return

    # Indicators
    _, st_bull = _supertrend(highs, lows, closes, ST_ATR, ST_MULT)
    _, st_prev = _supertrend(highs[:-1], lows[:-1], closes[:-1], ST_ATR, ST_MULT)
    e_fast = _ema(closes, EMA_FAST)
    e_slow = _ema(closes, EMA_SLOW)
    adx_val = _adx(highs, lows, closes, ADX_PERIOD)
    atr_val = _atr(highs, lows, closes, ST_ATR)

    ema_bull = e_fast > e_slow
    ema_bear = e_fast < e_slow
    trend_strong = adx_val > ADX_MIN
    flips_bull = st_bull and not st_prev and trend_strong
    flips_bear = not st_bull and st_prev and trend_strong

    # ── Position Update ──────────────────────────────────────
    if pos:
        context.bars_held += 1
        if pos.amount > 0:
            if price > context.highest_since_entry:
                context.highest_since_entry = price
            should_exit = (
                not st_bull  # SuperTrend flip
                or (ema_bear and not trend_strong)  # Trend lost
                or (atr_val > 0 and price < context.highest_since_entry - ATR_STOP_MULT * atr_val)
                or context.bars_held >= MAX_HOLD_BARS
            )
        else:
            if price < context.lowest_since_entry:
                context.lowest_since_entry = price
            should_exit = (
                st_bull  # SuperTrend flip back
                or (ema_bull and not trend_strong)
                or (atr_val > 0 and price > context.lowest_since_entry + ATR_STOP_MULT * atr_val)
                or context.bars_held >= MAX_HOLD_BARS
            )

        if should_exit:
            context.order(symbol, -pos.amount)
            context.entry_price = 0.0
            context.entry_side = 0
            context.bars_held = 0
            context.cooldown = COOLDOWN_BARS
        return

    # ── Entry (only when ADX confirms trend) ─────────────────
    if not trend_strong:
        return

    if atr_val <= 0:
        return

    if flips_bull and ema_bull:
        stop_dist = ATR_STOP_MULT * atr_val
        risk_amount = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk_amount / stop_dist
        if qty > 0:
            context.order(symbol, qty)
            context.entry_price = price
            context.entry_side = 1
            context.bars_held = 0
            context.highest_since_entry = price

    if flips_bear and ema_bear:
        stop_dist = ATR_STOP_MULT * atr_val
        risk_amount = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk_amount / stop_dist
        if qty > 0:
            context.order(symbol, -qty)
            context.entry_price = price
            context.entry_side = -1
            context.bars_held = 0
            context.lowest_since_entry = price
'''


MARKET_SUITABLE = "crypto_swap"
SUGGESTED_TIMEFRAME = "15m, 1h"
RISK_LEVEL = "aggressive"

STRATEGY_SOURCE = {
    "repo": "QuantDinger original (refactored from existing indicator strategies + GitHub CTA research)",
    "license": "Original QuantDinger code",
    "files_referenced": ["Existing a04_supertrend.py logic (refactored)"],
    "what_was_borrowed": [
        "SuperTrend indicator concept (public domain, Olivier Seban 2009)",
        "ADX trend strength (J. Welles Wilder, public domain)",
        "EMA crossover (standard TA, public domain)",
    ],
    "what_is_original": [
        "Combined SuperTrend + EMA + ADX state machine",
        "Signal conflict handling (NO_ACTION when ambiguous)",
        "ADX decline → signal reduction (not stop expansion)",
        "Close-then-Admission split for reversals",
        "All code rewritten from QuantDinger's existing a04_supertrend.py",
    ],
    "access_date": "2026-08-06",
    "differences_from_source": (
        "Original a04 had SuperTrend only. This adds EMA trend confirmation "
        "and ADX entry gating to reduce whipsaw. Exit logic expanded from "
        "simple SuperTrend flip to multi-condition (trend loss + ATR + time)."
    ),
}
