"""Dual EMA + Volume strategy."""
STRATEGY_CODE = r'''
"""
Dual EMA + Volume Surge
EMA(5,20) crossover with volume confirmation.
"""
# @param fast int 5 range=3:15:1
# @param slow int 20 range=10:50:5
# @param vol_factor float 1.5 range=1.0:3.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)
    g.prev_fast = None; g.prev_slow = None

def handle_data(context, data):
    fast = int(context.params.get("fast", 5))
    slow = int(context.params.get("slow", 20))
    vf = float(context.params.get("vol_factor", 1.5))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(slow + 30, "15m", ["close", "volume"], g.symbol)
    if len(bars) < slow + 5: return
    c = bars["close"]; v = bars["volume"]
    f_ema = _ema(c, fast); s_ema = _ema(c, slow)
    avg_vol = float(v.tail(slow).mean())
    cur_vol = float(v.iloc[-1])
    surge = cur_vol > avg_vol * vf
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = g.prev_fast is not None and g.prev_fast <= g.prev_slow and f_ema > s_ema and surge
    sell = g.prev_fast is not None and g.prev_fast >= g.prev_slow and f_ema < s_ema
    g.prev_fast = f_ema; g.prev_slow = s_ema
    if buy and amt <= 0:
        order_target_percent(g.symbol, tp, reason="ema_vol_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="ema_vol_sell")

def _ema(series, period):
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])
'''
