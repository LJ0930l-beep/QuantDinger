"""FUT-02: SuperTrend + EMA + ADX State Strategy (configurable, leverage enabled).

Source: freqtrade/freqtrade-strategies (GPL-3.0 ideas studied, NOT copied)
License: GPL-3.0 ideas only, no code copied.

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

def _supertrend(highs, lows, closes, period, mult):
    if len(closes) < period: return False
    atr_v = _atr(highs, lows, closes, period)
    if atr_v == 0: return False
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
    return trend[-1]

MAX_LOOKBACK = 250
MIN_REQUIRED = 100

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
    context.allow_leverage(max_leverage=10.0, min_leverage=1.0)
    context.set_universe(["Crypto:BTC/USDT@swap"])
    context.subscribe(frequency="15m", symbols=["Crypto:BTC/USDT@swap"])
    context.set_warmup(MAX_LOOKBACK + 5)


def handle_data(context):
    frequency = str(context.params.get("frequency", "15m"))
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

    st_bull = _supertrend(highs, lows, closes, ST_ATR, ST_MULT)
    st_prev = _supertrend(highs[:-1], lows[:-1], closes[:-1], ST_ATR, ST_MULT)
    e_f = _ema(closes, EMA_FAST)
    e_s = _ema(closes, EMA_SLOW)
    adx = _adx(highs, lows, closes, ADX_PERIOD)
    atr_v = _atr(highs, lows, closes, ST_ATR)

    ema_bull = e_f > e_s
    ema_bear = e_f < e_s
    strong = adx > ADX_MIN
    flips_bull = st_bull and not st_prev and strong
    flips_bear = not st_bull and st_prev and strong

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

    if not strong: return
    if atr_v <= 0: return

    if pos and pos.amount > 0:
        cs.bars_held += 1
        if price > cs.highest: cs.highest = price
        if (not st_bull
            or (ema_bear and not strong)
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
        if (st_bull
            or (ema_bull and not strong)
            or (atr_v > 0 and price > cs.lowest + ATR_STOP_MULT * atr_v)
            or cs.bars_held >= MAX_HOLD_BARS):
            context.order(symbol, -pos.amount)
            cs.in_pos = False
            cs.bars_held = 0
            cs.cooldown = COOLDOWN_BARS
        return

    if flips_bull and ema_bull:
        stop_dist = ATR_STOP_MULT * atr_v
        risk = context.portfolio.cash * RISK_BUDGET_PCT
        qty = risk / stop_dist
        if qty > 0:
            context.order(symbol, qty)
            cs.in_pos = True
            cs.entry_price = price
            cs.highest = price
            cs.bars_held = 0

    if flips_bear and ema_bear:
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
SUGGESTED_TIMEFRAME = "15m, 1h"
RISK_LEVEL = "aggressive"
