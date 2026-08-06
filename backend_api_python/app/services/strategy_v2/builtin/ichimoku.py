"""Ichimoku Cloud strategy."""
STRATEGY_CODE = r'''
"""
Ichimoku Cloud
Tenkan-sen / Kijun-sen cross + price vs cloud.
"""
# @param tenkan int 9 range=5:30:1
# @param kijun int 26 range=10:60:1
# @param senkou_b int 52 range=20:100:5
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(150)

def handle_data(context, data):
    t = int(context.params.get("tenkan", 9))
    k = int(context.params.get("kijun", 26))
    sb = int(context.params.get("senkou_b", 52))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(sb + 30, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < sb + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    tenken = (float(h.iloc[-t:].max()) + float(l.iloc[-t:].min())) / 2
    kijun = (float(h.iloc[-k:].max()) + float(l.iloc[-k:].min())) / 2
    sa = (float(h.iloc[-t:].max()) + float(l.iloc[-t:].min()) + float(h.iloc[-k:].max()) + float(l.iloc[-k:].min())) / 4
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = tenken > kijun and price > sa
    sell = tenken < kijun
    if buy and amt <= 0:
        order_target_percent(g.symbol, tp, reason="ichimoku_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="ichimoku_sell")
'''
