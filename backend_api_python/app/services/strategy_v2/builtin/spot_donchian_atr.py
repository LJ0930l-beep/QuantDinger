"""SPOT-01: Donchian + ATR Trend Breakout (configurable symbol/timeframe/leverage).

Source: jesse-ai/example-strategies (MIT)
License: MIT ideas studied, independently reimplemented.

User-configurable params at deployment:
  - frequency: "1m"/"5m"/"15m"/"30m"/"1h"/"4h"/"1d"
  - symbol: "Crypto:BTC/USDT@spot", "Crypto:ETH/USDT@spot", etc.
  - leverage: 1 (spot); >1 auto-switches to swap
"""

STRATEGY_CODE = """
import math

def _sma(values, period):
    if len(values) < period: return 0.0
    return sum(values[-period:]) / period

def _std(values, period):
    if len(values) < period: return 0.0
    avg = _sma(values, period)
    return math.sqrt(sum((v - avg) ** 2 for v in values[-period:]) / period)

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

ENTRY_WINDOW = 40
EXIT_WINDOW = 20
ATR_PERIOD = 14
ATR_STOP_MULT = 2.5
EMA_FAST = 50
EMA_SLOW = 200
VOLUME_WINDOW = 30
VOL_MULTIPLIER = 1.2
MAX_HOLD_BARS = 96
RISK_BUDGET_PCT = 0.015
COOLDOWN_BARS = 8


def initialize(context):
    # Manifest defaults — runtime overrides via deployment config params
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m", symbols=["Crypto:BTC/USDT@spot"])
    context.set_warmup(MAX_LOOKBACK + 5)


def handle_data(context):
    # Read user-configurable parameters at runtime
    frequency = str(context.params.get("frequency", "15m"))
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    leverage = float(context.params.get("leverage", 1))
    if leverage > 1 and "@spot" in symbol:
        symbol = symbol.replace("@spot", "@swap")
    bars = get_history(MAX_LOOKBACK, frequency, ["open","high","low","close","volume"], symbol)
    if bars is None or len(bars) < MIN_REQUIRED:
        return
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    volumes = [b["volume"] for b in bars]
    price = closes[-1]

    dh = max(highs[-ENTRY_WINDOW-1:-1])
    dl_exit = min(lows[-EXIT_WINDOW-1:-1])
    ema_f = _ema(closes, EMA_FAST)
    ema_s = _ema(closes, EMA_SLOW)
    atr_v = _atr(highs, lows, closes, ATR_PERIOD)
    window = volumes[-VOLUME_WINDOW-1:-1]
    vol_med = sorted(window)[len(window)//2] if window else 0
    cur_vol = volumes[-1]

    pos = context.portfolio.positions.get(symbol)
    cs = context.state
    if not hasattr(cs, "in_pos"):
        cs.in_pos = False
        cs.entry_price = 0.0
        cs.highest = 0.0
        cs.bars_held = 0
        cs.cooldown = 0

    if cs.cooldown > 0:
        cs.cooldown -= 1
        return

    if pos and pos.amount > 0:
        cs.bars_held += 1
        if price > cs.highest:
            cs.highest = price
        if (price < dl_exit
            or (atr_v > 0 and price < cs.highest - ATR_STOP_MULT * atr_v)
            or cs.bars_held >= MAX_HOLD_BARS):
            context.order(symbol, -pos.amount)
            cs.in_pos = False
            cs.entry_price = 0.0
            cs.bars_held = 0
            cs.cooldown = COOLDOWN_BARS
        return

    breakout = price > dh
    trend = ema_f > ema_s
    vol_ok = cur_vol > vol_med * VOL_MULTIPLIER if vol_med > 0 else False
    atr_ok = atr_v > 0 and (atr_v / price) > 0.001

    if breakout and trend and vol_ok and atr_ok and atr_v > 0:
        stop_dist = ATR_STOP_MULT * atr_v
        risk = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk / stop_dist
        if qty > 0:
            context.order(symbol, qty)
            cs.in_pos = True
            cs.entry_price = price
            cs.highest = price
            cs.bars_held = 0
"""

MARKET_SUITABLE = "crypto_spot, crypto_swap"
SUGGESTED_TIMEFRAME = "15m, 30m, 1h, 4h"
RISK_LEVEL = "conservative"
