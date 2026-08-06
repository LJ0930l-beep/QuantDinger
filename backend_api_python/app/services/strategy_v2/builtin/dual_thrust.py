"""Dual Thrust breakout strategy."""
STRATEGY_CODE = r'''
"""
Dual Thrust Breakout
Classic range breakout system.
"""
# @param lookback int 4 range=2:20:1
# @param k1 float 0.5 range=0.3:1.0:0.1
# @param k2 float 0.5 range=0.3:1.0:0.1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(30)

def handle_data(context, data):
    lb = int(context.params.get("lookback", 4))
    k1 = float(context.params.get("k1", 0.5))
    k2 = float(context.params.get("k2", 0.5))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(lb + 10, "15m", ["high", "low", "close", "open"], g.symbol)
    if len(bars) < lb + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]; o = bars["open"]
    hh = float(h.iloc[-lb-1:-1].max()); ll = float(l.iloc[-lb-1:-1].min())
    hc = float(c.iloc[-lb-1:-1].max()); lc = float(c.iloc[-lb-1:-1].min())
    rng = max(hh - lc, hc - ll)
    op = float(o.iloc[-1])
    upper = op + k1 * rng; lower = op - k2 * rng
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if price >= upper and amt <= 0:
        order_target_percent(g.symbol, tp, reason="dual_thrust_buy")
    elif price <= lower and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="dual_thrust_sell")
'''
