"""FUT-01: Turtle Bidirectional Trend (configurable, leverage enabled).

Source: jesse-ai/example-strategies (MIT) / Richard Dennis 1983 (public domain)
License: MIT ideas studied, independently reimplemented.

User-configurable params at deployment:
  - frequency, symbol, leverage (1-N for contracts)
"""

STRATEGY_CODE = """
import math

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

MAX_LOOKBACK = 250
MIN_REQUIRED = 205

ENTRY_WINDOW = 55
EXIT_WINDOW = 20
ATR_PERIOD = 20
ATR_STOP_MULT = 2.5
RISK_BUDGET_PCT = 0.0035
MAX_HOLD_BARS = 192
COOLDOWN_BARS = 6
FUNDING_GUARD = 0.001
EMA_TREND = 200


def initialize(context):
    context.allow_leverage(max_leverage=10.0, min_leverage=1.0)
    context.set_universe(["Crypto:BTC/USDT@swap"])
    context.subscribe(frequency="1h", symbols=["Crypto:BTC/USDT@swap"])
    context.set_warmup(MAX_LOOKBACK + 5)


def handle_data(context):
    frequency = str(context.params.get("frequency", "1h"))
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@swap"))
    leverage = float(context.params.get("leverage", 3))
    if leverage > 0:
        context.allow_leverage(max_leverage=leverage, min_leverage=1.0)
    bars = get_history(MAX_LOOKBACK, frequency, ["open","high","low","close","volume"], symbol)
    if bars is None or len(bars) < MIN_REQUIRED:
        return
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    price = closes[-1]

    dh_entry = max(highs[-ENTRY_WINDOW-1:-1])
    dl_entry = min(lows[-ENTRY_WINDOW-1:-1])
    dl_exit = min(lows[-EXIT_WINDOW-1:-1])
    dh_exit = max(highs[-EXIT_WINDOW-1:-1])
    atr_v = _atr(highs, lows, closes, ATR_PERIOD)
    ema_trend = _ema(closes, EMA_TREND)
    funding_proxy = (price - ema_trend) / ema_trend if ema_trend > 0 else 0
    funding_extreme = abs(funding_proxy) > FUNDING_GUARD

    pos = context.portfolio.positions.get(symbol)
    cs = context.state
    if not hasattr(cs, "in_pos"):
        cs.in_pos = False
        cs.entry_price = 0.0
        cs.highest = 0.0
        cs.lowest = float("inf")
        cs.bars_held = 0
        cs.cooldown = 0

    if cs.cooldown > 0:
        cs.cooldown -= 1
        return

    if pos and pos.amount > 0:
        cs.bars_held += 1
        if price > cs.highest: cs.highest = price
        if (price <= dl_exit
            or (atr_v > 0 and price < cs.highest - ATR_STOP_MULT * atr_v)
            or cs.bars_held >= MAX_HOLD_BARS):
            context.order(symbol, -pos.amount)
            cs.in_pos = False
            cs.bars_held = 0
            cs.cooldown = COOLDOWN_BARS
        return

    if pos and pos.amount < 0:
        cs.bars_held += 1
        if price < cs.lowest: cs.lowest = price
        if (price >= dh_exit
            or (atr_v > 0 and price > cs.lowest + ATR_STOP_MULT * atr_v)
            or cs.bars_held >= MAX_HOLD_BARS):
            context.order(symbol, -pos.amount)
            cs.in_pos = False
            cs.bars_held = 0
            cs.cooldown = COOLDOWN_BARS
        return

    if atr_v <= 0: return

    long_break = price > dh_entry
    short_break = price < dl_entry
    if long_break and short_break: return

    long_blocked = funding_extreme and funding_proxy > 0
    short_blocked = funding_extreme and funding_proxy < 0

    if long_break and not long_blocked:
        stop_dist = ATR_STOP_MULT * atr_v
        risk = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk / stop_dist
        if qty > 0:
            context.order(symbol, qty)
            cs.in_pos = True
            cs.entry_price = price
            cs.highest = price
            cs.bars_held = 0

    if short_break and not short_blocked:
        stop_dist = ATR_STOP_MULT * atr_v
        risk = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk / stop_dist
        if qty > 0:
            context.order(symbol, -qty)
            cs.in_pos = True
            cs.entry_price = price
            cs.lowest = price
            cs.bars_held = 0
"""

MARKET_SUITABLE = "crypto_swap"
SUGGESTED_TIMEFRAME = "15m, 1h, 4h"
RISK_LEVEL = "aggressive"
