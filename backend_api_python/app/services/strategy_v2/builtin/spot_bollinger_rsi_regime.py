"""SPOT-02: Bollinger + RSI + Regime Mean Reversion (configurable).

Source: freqtrade/freqtrade-strategies (GPL-3.0 ideas studied, NOT copied)
License: GPL-3.0 ideas only, no code copied.

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

def _adx(highs, lows, closes, period):
    if len(closes) < period * 2: return 25.0
    tr = []; pd = []; nd = []
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
    s = pdi + ndi
    return abs(pdi - ndi) / s * 100 if s > 0 else 0

MAX_LOOKBACK = 250
MIN_REQUIRED = 205

BB_WINDOW = 20
BB_STD = 2.0
RSI_PERIOD = 14
RSI_ENTRY = 28
RSI_EXIT = 52
ADX_MAX = 22
ATR_PERIOD = 14
ATR_STOP_MULT = 2.0
EMA_LONG = 200
MAX_HOLD_BARS = 32
RISK_BUDGET_PCT = 0.01
COOLDOWN_BARS = 12
FLASH_ATR_MULT = 5.0


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

    mid = _sma(closes, BB_WINDOW)
    stdev = _std(closes, BB_WINDOW)
    lower = mid - BB_STD * stdev
    upper = mid + BB_STD * stdev
    rsi = _rsi(closes, RSI_PERIOD)
    adx = _adx(highs, lows, closes, ADX_MAX)
    atr = _atr(highs, lows, closes, ATR_PERIOD)
    ema200 = _ema(closes, EMA_LONG)

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
        if (price >= mid
            or rsi >= RSI_EXIT
            or cs.bars_held >= MAX_HOLD_BARS
            or (atr > 0 and price < cs.entry_price - ATR_STOP_MULT * atr)
            or (adx > ADX_MAX and price < ema200)):
            context.order(symbol, -pos.amount)
            cs.in_pos = False
            cs.entry_price = 0.0
            cs.bars_held = 0
            cs.cooldown = COOLDOWN_BARS
        return

    if not (price <= lower): return
    if not (rsi < RSI_ENTRY): return
    if not (adx < ADX_MAX): return
    if not (price > ema200 * 0.95): return
    bar_range = highs[-1] - lows[-1]
    if atr > 0 and bar_range > FLASH_ATR_MULT * atr: return
    if volumes[-1] <= 0: return

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

MARKET_SUITABLE = "crypto_spot, crypto_swap"
SUGGESTED_TIMEFRAME = "5m, 15m, 1h"
RISK_LEVEL = "conservative"
