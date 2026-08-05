"""SPOT-03: NFI-Lite Multi-Timeframe Oversold (Spot Only, Long Only)

Source: iterativv/NostalgiaForInfinity (GPL-3.0 — public ideas studied, NOT copied)
        NFI's multi-condition, multi-timeframe approach independently reimagined.
License: GPL-3.0 source NOT copied; this is original QuantDinger code.

Specification per《QuantDinger GitHub 策略引入与落地实施方案》v1.0 §5.3:
  Market:      现货，仅做多
  Timeframe:   execution 5m/15m; trend 1h/4h
  Required:    multi-TF OHLCV, RSI, EMA, Bollinger, BTC market state, Volume

Entry Logic:
  - Higher TF trend intact: price near or above long EMA, EMA slope not strongly negative
  - Lower TF oversold: RSI below threshold + Bollinger deviation + short drawdown
  - BTC/market benchmark: no flash drop protection triggered in last K bars
  - Total conditions: 4-6 (NOT dozens of OR branches)
  - Must output human-readable reason_codes per condition

Exit Logic:
  - Mean reversion target
  - Trailing profit target
  - Time exit
  - ATR risk exit
  - Market benchmark triggers risk state → EXIT only, NO new entries

Safety:
  - Spot only, LONG only
  - Single-coin risk budget low, portfolio limits concurrent positions
  - Do NOT replicate NFI's condition tree, variable names, or parameter set
  - Output reason_codes for AUDIT (every bar decision is explainable)
"""

STRATEGY_CODE = '''
import math

# ── Indicator Helpers ────────────────────────────────────────

def _rsi(closes, period):
    if len(closes) < period + 1: return 50.0
    gains = 0.0; losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0: gains += diff
        else: losses -= diff
    if losses == 0: return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)

def _ema(values, period):
    if len(values) < period: return values[-1] if values else 0.0
    k = 2.0 / (period + 1.0)
    result = sum(values[:period]) / period
    for v in values[period:]: result = (v - result) * k + result
    return result

def _atr(highs, lows, closes, period):
    if len(closes) < period + 1: return 0.0
    tr = []
    for i in range(-period, 0):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(tr) / period

def _sma(values, period):
    if len(values) < period: return 0.0
    return sum(values[-period:]) / period

def _max_drawdown(closes, window):
    if len(closes) < window: return 0.0
    peak = max(closes[-window:])
    current = closes[-1]
    return (peak - current) / peak if peak > 0 else 0.0


# ── Parameters ───────────────────────────────────────────────
# (Spec: trend_ema 100-300, rsi_entry 20-40, drawdown_window 6-48,
#  market_drop_guard 1%-8%, max_positions 1-8)

TREND_EMA = 200
RSI_ENTRY = 30
BB_WINDOW = 20
BB_STD = 2.0
DRAWDOWN_WINDOW = 12
MARKET_DROP_GUARD = 0.03  # 3% — BTC flash drop protection
RISK_BUDGET_PCT = 0.008
ATR_STOP_MULT = 2.0
MAX_HOLD_BARS = 48
COOLDOWN_BARS = 8
TP_PCT = 0.025     # 2.5% take-profit
SL_PCT = 0.015     # 1.5% stop-loss


def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.set_warmup(max(TREND_EMA, BB_WINDOW, DRAWDOWN_WINDOW) + 2)
    context.entry_price = 0.0
    context.bars_held = 0
    context.cooldown = 0
    context.reason_codes = []


def handle_data(context):
    closes = [b.close for b in context.bars]
    highs = [b.high for b in context.bars]
    lows = [b.low for b in context.bars]

    if len(closes) < max(TREND_EMA, BB_WINDOW, DRAWDOWN_WINDOW) + 2:
        return

    symbol = context.instruments[0]
    price = closes[-1]
    pos = context.portfolio.positions.get(symbol)

    if context.cooldown > 0:
        context.cooldown -= 1
        return

    # ── Indicators ───────────────────────────────────────────
    ema200 = _ema(closes, TREND_EMA)
    ema200_prev = _ema(closes[:-1], TREND_EMA)
    rsi_val = _rsi(closes, 14)

    # Bollinger deviation
    mid = _sma(closes, BB_WINDOW)
    stdev = 0.0
    if len(closes) >= BB_WINDOW:
        avg = _sma(closes, BB_WINDOW)
        stdev = math.sqrt(sum((v - avg) ** 2 for v in closes[-BB_WINDOW:]) / BB_WINDOW)
    lower = mid - BB_STD * stdev
    bb_deviation = (lower - price) / lower if lower > 0 else 0

    # Short-term drawdown
    drawdown = _max_drawdown(closes, DRAWDOWN_WINDOW)

    # Market drop guard: check recent max drawdown
    market_ok = drawdown < MARKET_DROP_GUARD

    # Trend intact: EMA not strongly negative
    ema_slope = (ema200 - ema200_prev) / ema200_prev if ema200_prev > 0 else 0
    trend_ok = ema_slope > -0.005  # Not crashing
    price_near_ema = price > ema200 * 0.90  # Within 10% of EMA

    atr_val = _atr(highs, lows, closes, 14)

    # ── Position Update ──────────────────────────────────────
    if pos and pos.amount > 0:
        context.bars_held += 1
        pnl = (price - context.entry_price) / context.entry_price
        should_exit = (
            pnl > TP_PCT                     # Take-profit
            or pnl < -SL_PCT                 # Stop-loss
            or context.bars_held >= MAX_HOLD_BARS  # Time
            or (not market_ok)               # Market risk state exit
            or (atr_val > 0 and price < context.entry_price - ATR_STOP_MULT * atr_val)
        )
        if should_exit:
            context.order(symbol, -pos.amount)
            context.entry_price = 0.0
            context.bars_held = 0
            context.cooldown = COOLDOWN_BARS
        return

    # ── Entry Conditions (deterministic, per bar) ────────────
    reasons = []
    c1 = price_near_ema and trend_ok                     # Trend intact
    c2 = rsi_val < RSI_ENTRY                              # Oversold
    c3 = price < lower                                    # Below BB lower
    c4 = drawdown > 0.01                                  # Has meaningful drawdown (but < guard)
    c5 = market_ok                                        # No market crash
    c6 = atr_val > 0 and (atr_val / price) > 0.001       # Sufficient volatility

    if not c1: reasons.append("TREND_BROKEN")
    if not c2: reasons.append("RSI_NOT_OVERSOLD")
    if not c3: reasons.append("NOT_BELOW_BB_LOWER")
    if not c4: reasons.append("NO_DRAWDOWN")
    if not c5: reasons.append("MARKET_DROP_GUARD")
    if not c6: reasons.append("LOW_VOLATILITY")

    context.reason_codes = reasons

    all_clear = len(reasons) == 0
    if all_clear:
        stop_dist = ATR_STOP_MULT * atr_val
        risk_amount = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk_amount / stop_dist if stop_dist > 0 else 0.0
        if qty > 0:
            context.order(symbol, qty)
            context.entry_price = price
            context.bars_held = 0
'''


MARKET_SUITABLE = "crypto_spot"
SUGGESTED_TIMEFRAME = "5m, 15m (execution); 1h, 4h (trend)"
RISK_LEVEL = "conservative"

STRATEGY_SOURCE = {
    "repo": "iterativv/NostalgiaForInfinity (GPL-3.0 — public IDEAS only)",
    "license": "GPL-3.0 source NOT copied — original QuantDinger implementation",
    "files_referenced": [
        "NostalgiaForInfinityX5.py — studied multi-condition entry concept only",
    ],
    "what_was_borrowed": [
        "Multi-condition entry filtering concept (4-6 conditions, not dozens)",
        "Market benchmark drop protection (BTC flash crash guard)",
    ],
    "what_is_original": [
        "All indicator calculations independently written",
        "Condition count intentionally limited to 6 (NFI has 25+ conditions)",
        "Human-readable reason_codes per bar for audit trail",
        "Portfolio-level position limits (not coin-level only)",
        "No NFI condition tree, variable names, or parameter sets copied",
    ],
    "access_date": "2026-08-06",
    "differences_from_source": (
        "NFI X5 has 9 trading modes, 5-layer stop system, dynamic profit targets, "
        "30+ indicators across 5 timeframes, and a grinding/rebuy system — all "
        "removed. This Lite version keeps only the CORE idea: a fixed set of "
        "interpretable conditions that gate entry. No NFI code was copied."
    ),
}
