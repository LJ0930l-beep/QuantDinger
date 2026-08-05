"""Parabolic SAR strategy."""
STRATEGY_CODE = r'''
"""
Parabolic SAR + EMA Filter
Trend following with SAR flip and EMA(50) trend filter.
"""
# @param sar_step float 0.02 range=0.01:0.05:0.005
# @param sar_max float 0.2 range=0.1:0.5:0.05
# @param ema_filter int 50 range=20:200:10
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)
    g.sar_prev = None

def handle_data(context, data):
    step = float(context.params.get("sar_step", 0.02))
    mx = float(context.params.get("sar_max", 0.2))
    ema_p = int(context.params.get("ema_filter", 50))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(ema_p + 30, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < ema_p + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    ema50 = float(c.tail(ema_p).mean())
    sar = _sar(h, l, step, mx)
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = price > sar and price > ema50
    sell = price < sar
    if buy and amt <= 0:
        order_target_percent(g.symbol, tp, reason="sar_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="sar_sell")

def _sar(high, low, step, mx):
    n = len(high); sar = float(low.iloc[0]); ep = float(high.iloc[0])
    af = step; uptrend = True
    for i in range(1, n):
        h = float(high.iloc[i]); l = float(low.iloc[i])
        if uptrend:
            sar = min(sar + af * (ep - sar), float(low.iloc[i-1]), float(low.iloc[max(0,i-2)]))
            if h > ep: ep = h; af = min(af + step, mx)
            if l <= sar: uptrend = False; sar = ep; ep = l; af = step
        else:
            sar = max(sar - af * (sar - ep), float(high.iloc[i-1]), float(high.iloc[max(0,i-2)]))
            if l < ep: ep = l; af = min(af + step, mx)
            if h >= sar: uptrend = True; sar = ep; ep = h; af = step
    return sar
'''
