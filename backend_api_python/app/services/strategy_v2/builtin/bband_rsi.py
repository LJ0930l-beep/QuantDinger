"""Bollinger Bands + RSI mean reversion strategy.
Buy when price touches lower BB (2 std) and RSI < 30.
Sell when price touches upper BB or RSI > 70.
Source: freqtrade-strategies BbandRsi
"""
STRATEGY_CODE = r'''
"""
Bollinger Bands + RSI
Mean reversion: buy oversold, sell overbought.
"""

# @param bb_window int 20 range=10:50:5
# @param bb_std float 2.0 range=1.0:3.0:0.25
# @param rsi_period int 14 range=7:30:1
# @param rsi_low int 30 range=20:40:5
# @param rsi_high int 70 range=60:80:5
# @param target_pct float 0.95 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)

def handle_data(context, data):
    w = int(context.params.get("bb_window", 20))
    std = float(context.params.get("bb_std", 2.0))
    rp = int(context.params.get("rsi_period", 14))
    rl = int(context.params.get("rsi_low", 30))
    rh = int(context.params.get("rsi_high", 70))
    tp = float(context.params.get("target_pct", 0.95))
    bars = get_history(max(w, rp) + 10, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < w + 5:
        return
    close = bars["close"]
    sma = float(close.tail(w).mean())
    st = float(close.tail(w).std())
    lower = sma - std * st
    upper = sma + std * st
    price = float(close.iloc[-1])
    rsi = _rsi(close, rp)
    pos = get_position(g.symbol)
    amt = float(pos.amount or 0.0)
    if price <= lower and rsi < rl and amt <= 0:
        order_target_percent(g.symbol, tp, reason="bb_rsi_buy")
    elif (price >= upper or rsi > rh) and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="bb_rsi_sell")

def _rsi(close, p):
    n = len(close)
    if n < p + 1: return 50.0
    g = []; l = []
    for i in range(1, n):
        d = float(close.iloc[i]) - float(close.iloc[i-1])
        g.append(d if d > 0 else 0.0)
        l.append(-d if d < 0 else 0.0)
    ag = sum(g[:p]) / p; al = sum(l[:p]) / p
    for i in range(p, len(g)):
        ag = (ag * (p - 1) + g[i]) / p
        al = (al * (p - 1) + l[i]) / p
    return 100.0 - (100.0 / (1.0 + ag / al)) if al > 0 else 100.0
'''
