"""RSI Scalper 5m strategy."""
STRATEGY_CODE = r'''
"""
RSI Scalper 5m
Pure RSI mean reversion: buy oversold, sell overbought.
"""
# @param rsi_period int 14 range=7:30:1
# @param rsi_low int 25 range=15:35:5
# @param rsi_high int 75 range=65:85:5
# @param target_pct float 1.0 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="5m")
    context.set_warmup(50)

def handle_data(context, data):
    rp = int(context.params.get("rsi_period", 14))
    rl = int(context.params.get("rsi_low", 25))
    rh = int(context.params.get("rsi_high", 75))
    tp = float(context.params.get("target_pct", 1.0))
    bars = get_history(rp + 10, "5m", ["close"], g.symbol)
    if len(bars) < rp + 5: return
    c = bars["close"]
    rsi = _rsi(c, rp)
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if rsi < rl and amt <= 0:
        order_target_percent(g.symbol, tp, reason="rsi_scalp_buy")
    elif rsi > rh and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="rsi_scalp_sell")

def _rsi(close, p):
    n = len(close)
    if n < p + 1: return 50.0
    g = []; l = []
    for i in range(1, n):
        d = float(close.iloc[i]) - float(close.iloc[i-1])
        g.append(d if d > 0 else 0.0); l.append(-d if d < 0 else 0.0)
    ag = sum(g[:p]) / p; al = sum(l[:p]) / p
    for i in range(p, len(g)):
        ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + l[i]) / p
    return 100.0 - (100.0 / (1.0 + ag / al)) if al > 0 else 100.0
'''
