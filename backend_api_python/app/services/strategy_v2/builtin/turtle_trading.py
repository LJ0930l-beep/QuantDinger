"""Turtle Trading strategy."""
STRATEGY_CODE = r'''
"""
Turtle Trading
Classic Donchian channel breakout.
"""
# @param entry_window int 20 range=10:55:5
# @param exit_window int 10 range=5:30:5
# @param target_pct float 0.8 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(80)

def handle_data(context, data):
    ew = int(context.params.get("entry_window", 20))
    xw = int(context.params.get("exit_window", 10))
    tp = float(context.params.get("target_pct", 0.8))
    bars = get_history(ew + 30, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < ew + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    entry_high = float(h.iloc[-ew:].max())
    exit_low = float(l.iloc[-xw:].min())
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if price >= entry_high and amt <= 0:
        order_target_percent(g.symbol, tp, reason="turtle_buy")
    elif price <= exit_low and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="turtle_sell")
'''
