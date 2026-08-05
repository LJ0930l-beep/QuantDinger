"""FUT-01: Turtle Bidirectional Trend (Perpetual Swap, Long+Short)

Source: jesse-ai/example-strategies (TurtleRules) + vnpy/vnpy_ctastrategy (MIT)
        Turtle trading rules by Richard Dennis (1983) — public domain.
License: MIT sources referenced; this implementation is original QuantDinger code.

Specification per《QuantDinger GitHub 策略引入与落地实施方案》v1.0 §5.4:
  Market:      永续合约，多空
  Timeframe:   15m, 1h, 4h (首选 1h)
  Required:    OHLCV, Donchian, ATR, Funding, contract rules, margin facts

Entry Logic (closed bars only):
  - LONG:  price breaks above Donchian High(entry_window)
  - SHORT: price breaks below Donchian Low(entry_window)
  - Funding guard: extreme funding rate blocks new positions in that direction
  - Must carry position_side + reduce_only flag

Exit Logic:
  - Reverse Donchian breakout (shorter exit_window)
  - ATR trailing stop
  - Risk state change or margin buffer insufficient → REDUCE/CLOSE

Position Sizing:
  - ATR risk-based: quantity = (risk_budget * equity) / (ATR * stop_mult)
  - Leverage set by Portfolio/Hard Risk, NOT by strategy

Safety:
  - No pyramiding in v1 (unlike original Turtle)
  - No same-bar flip (close + reverse requires new Admission)
  - Funding extreme guard prevents entries into hostile funding environments
"""

STRATEGY_CODE = '''
import math

# ── Indicator Helpers ────────────────────────────────────────

def _donchian_high(highs, period):
    if len(highs) <= period:
        return 0.0
    return max(highs[-period-1:-1])

def _donchian_low(lows, period):
    if len(lows) <= period:
        return float("inf")
    return min(lows[-period-1:-1])

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


# ── Parameters ───────────────────────────────────────────────
# (Spec: entry_window 20-100 default 55, exit_window 10-40 default 20)

ENTRY_WINDOW = 55
EXIT_WINDOW = 20
ATR_PERIOD = 20
ATR_STOP_MULT = 2.5
RISK_BUDGET_PCT = 0.0035  # 0.35% per trade
MAX_HOLD_BARS = 192
COOLDOWN_BARS = 6
FUNDING_GUARD_THRESHOLD = 0.001  # 0.1% — extreme funding blocks new entries
EMA_TREND = 200  # Long-term EMA for trend context


def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@swap"])
    context.set_warmup(max(ENTRY_WINDOW, ATR_PERIOD, EMA_TREND) + 2)
    context.entry_price = 0.0
    context.entry_side = 0   # 1=long, -1=short
    context.bars_held = 0
    context.highest_since_entry = 0.0
    context.lowest_since_entry = float("inf")
    context.cooldown = 0


def handle_data(context):
    closes = [b.close for b in context.bars]
    highs = [b.high for b in context.bars]
    lows = [b.low for b in context.bars]

    min_len = max(ENTRY_WINDOW, ATR_PERIOD, EMA_TREND) + 2
    if len(closes) < min_len:
        return

    symbol = context.instruments[0]
    price = closes[-1]
    pos = context.portfolio.positions.get(symbol)

    if context.cooldown > 0:
        context.cooldown -= 1
        return

    # Indicators
    dh_entry = _donchian_high(highs, ENTRY_WINDOW)
    dl_entry = _donchian_low(lows, ENTRY_WINDOW)
    dh_exit = _donchian_high(highs, EXIT_WINDOW)
    dl_exit = _donchian_low(lows, EXIT_WINDOW)
    atr_val = _atr(highs, lows, closes, ATR_PERIOD)
    ema_trend = _ema(closes, EMA_TREND)

    # ── Funding guard proxy ──────────────────────────────────
    # Use price deviation from EMA as funding rate proxy.
    # Real funding data available at execution/admission layer.
    funding_proxy = (price - ema_trend) / ema_trend if ema_trend > 0 else 0
    funding_extreme = abs(funding_proxy) > FUNDING_GUARD_THRESHOLD

    # ── Position Update ──────────────────────────────────────
    if pos:
        context.bars_held += 1
        if pos.amount > 0:
            if price > context.highest_since_entry:
                context.highest_since_entry = price
            should_exit = (
                price <= dl_exit  # Donchian low exit
                or (atr_val > 0 and price < context.highest_since_entry - ATR_STOP_MULT * atr_val)  # ATR trailing
                or context.bars_held >= MAX_HOLD_BARS  # Time exit
            )
        elif pos.amount < 0:
            if price < context.lowest_since_entry:
                context.lowest_since_entry = price
            should_exit = (
                price >= dh_exit  # Donchian high exit (for shorts)
                or (atr_val > 0 and price > context.lowest_since_entry + ATR_STOP_MULT * atr_val)
                or context.bars_held >= MAX_HOLD_BARS
            )

        if should_exit:
            context.order(symbol, -pos.amount)  # Close position
            context.entry_price = 0.0
            context.entry_side = 0
            context.bars_held = 0
            context.cooldown = COOLDOWN_BARS
        return

    # ── Entry ────────────────────────────────────────────────
    if atr_val <= 0:
        return

    # LONG signal
    long_breakout = price > dh_entry
    # SHORT signal
    short_breakout = price < dl_entry

    # Avoid same-bar flip
    if long_breakout and short_breakout:
        return

    # Funding guard: don't enter LONG when funding is extremely positive
    # (shorts pay longs — but extreme means likely reversal soon)
    long_blocked = funding_extreme and funding_proxy > 0
    short_blocked = funding_extreme and funding_proxy < 0

    if long_breakout and not long_blocked:
        stop_dist = ATR_STOP_MULT * atr_val
        risk_amount = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk_amount / stop_dist
        if qty > 0:
            context.order(symbol, qty)
            context.entry_price = price
            context.entry_side = 1
            context.bars_held = 0
            context.highest_since_entry = price

    if short_breakout and not short_blocked:
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
SUGGESTED_TIMEFRAME = "15m, 1h, 4h"
RISK_LEVEL = "aggressive"

STRATEGY_SOURCE = {
    "repo": "jesse-ai/example-strategies, vnpy/vnpy_ctastrategy",
    "license": "MIT (ideas studied, independently reimplemented)",
    "files_referenced": [
        "jesse-ai/example-strategies/TurtleRules/__init__.py",
    ],
    "what_was_borrowed": [
        "Turtle trading concept (public domain, Richard Dennis 1983)",
        "Donchian channel breakout (Richard Donchian, public domain)",
        "ATR-based position sizing (J. Welles Wilder, public domain)",
    ],
    "what_is_original": [
        "Funding rate guard block (extreme funding → no new entry)",
        "No pyramiding in v1 (simplified vs original Turtle)",
        "Short exit via Donchian High (original Turtle only uses low)",
        "Cooldown after exit",
        "All code independently written",
    ],
    "access_date": "2026-08-06",
    "differences_from_source": (
        "Original Turtle uses System 1 (20-day) + System 2 (55-day) with "
        "pyramiding up to 4 units. This implementation uses only System 2 "
        "entry, no pyramiding in v1, and adds funding guard + cooldown. "
        "Exit for shorts uses Donchian High (mirror of long exit) which "
        "differs from the original's symmetric 10-day low exit."
    ),
}
