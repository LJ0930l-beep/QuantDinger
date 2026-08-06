"""EMA Crossover — fast EMA crosses above slow EMA to buy, below to sell.
Uses 8/21 EMA classic crossover with 200 EMA trend filter.
Source: freqtrade community strategies
"""
STRATEGY_CODE = r'''
"""
EMA 8/21 Crossover with 200 EMA filter
Classic trend following: buy golden cross, sell death cross.
"""

# @param fast int 8 range=3:20:1
# @param slow int 21 range=10:50:1
# @param trend int 200 range=50:300:25
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(300)

def handle_data(context, data):
    fast = int(context.params.get("fast", 8))
    slow = int(context.params.get("slow", 21))
    trend = int(context.params.get("trend", 200))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(max(fast, slow, trend) + 10, "15m", ["close"], g.symbol)
    if len(bars) < trend + 5: return
    c = bars["close"]
    ema_fast = _ema(c, fast)
    ema_slow = _ema(c, slow)
    ema_trend = _ema(c, trend)
    price = float(c.iloc[-1])
    pos = get_position(g.symbol)
    amt = float(pos.amount or 0.0)
    if ema_fast > ema_slow and price > ema_trend and amt <= 0:
        order_target_percent(g.symbol, tp, reason="ema_cross_buy")
    elif ema_fast < ema_slow and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="ema_cross_sell")

def _ema(series, period):
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])
'''
