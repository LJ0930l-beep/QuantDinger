"""SPOT-03: NFI-Lite Multi-Timeframe Oversold (configurable).

Source: iterativv/NostalgiaForInfinity (GPL-3.0 — public ideas studied, NOT copied)

User-configurable params at deployment:
  - frequency, symbol, leverage
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

def _rsi(closes, period):
    if len(closes) < period + 1: return 50.0
    gains = 0.0; losses = 0.0
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0: gains += diff
        else: losses -= diff
    if losses == 0: return 100.0
    return 100.0 - 100.0 / (1.0 + gains / losses)

def _atr(highs, lows, closes, period):
    if len(closes) < period + 1: return 0.0
    tr = []
    for i in range(-period, 0):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(tr) / period

MAX_LOOKBACK = 250
MIN_REQUIRED = 205

TREND_EMA = 200
RSI_ENTRY = 30
BB_WINDOW = 20
BB_STD = 2.0
DRAWDOWN_WINDOW = 12
MARKET_DROP_GUARD = 0.03
ATR_PERIOD = 14
ATR_STOP_MULT = 2.0
MAX_HOLD_BARS = 48
COOLDOWN_BARS = 8
TP_PCT = 0.025
SL_PCT = 0.015
RISK_BUDGET_PCT = 0.008


def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m", symbols=["Crypto:BTC/USDT@spot"])
    context.set_warmup(MAX_LOOKBACK + 5)


def handle_data(context):
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

    ema200 = _ema(closes, TREND_EMA)
    closes_prev = closes[:-1]
    if len(closes_prev) >= TREND_EMA:
        ema200_prev = _ema(closes_prev, TREND_EMA)
    else:
        ema200_prev = ema200
    rsi = _rsi(closes, 14)
    avg = _sma(closes, BB_WINDOW)
    stdev = _std(closes, BB_WINDOW)
    lower = avg - BB_STD * stdev
    atr = _atr(highs, lows, closes, ATR_PERIOD)

    if len(closes) >= DRAWDOWN_WINDOW:
        peak = max(closes[-DRAWDOWN_WINDOW:])
        drawdown = (peak - price) / peak if peak > 0 else 0
    else:
        drawdown = 0

    pos = context.portfolio.positions.get(symbol)
    cs = context.state
    if not hasattr(cs, "in_pos"):
        cs.in_pos = False
        cs.entry_price = 0.0
        cs.bars_held = 0
        cs.cooldown = 0

    if cs.cooldown > 0:
        cs.cooldown -= 1
        return

    if pos and pos.amount > 0:
        cs.bars_held += 1
        pnl = (price - cs.entry_price) / cs.entry_price if cs.entry_price > 0 else 0
        if (pnl > TP_PCT or pnl < -SL_PCT
            or cs.bars_held >= MAX_HOLD_BARS
            or (atr > 0 and price < cs.entry_price - ATR_STOP_MULT * atr)
            or drawdown > MARKET_DROP_GUARD):
            context.order(symbol, -pos.amount)
            cs.in_pos = False
            cs.entry_price = 0.0
            cs.bars_held = 0
            cs.cooldown = COOLDOWN_BARS
        return

    if not (price > ema200 * 0.90): return
    if ema200_prev > 0 and (ema200 - ema200_prev) / ema200_prev <= -0.005: return
    if rsi >= RSI_ENTRY: return
    if price >= lower: return
    if drawdown < 0.01: return
    if drawdown > MARKET_DROP_GUARD: return
    if atr <= 0 or (atr / price) < 0.001: return

    if atr > 0:
        stop_dist = ATR_STOP_MULT * atr
        risk = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk / stop_dist
        if qty > 0:
            context.order(symbol, qty)
            cs.in_pos = True
            cs.entry_price = price
            cs.bars_held = 0
"""

MARKET_SUITABLE = "crypto_spot"
SUGGESTED_TIMEFRAME = "5m, 15m, 1h"
RISK_LEVEL = "conservative"
