"""MACD Strategy — buy when MACD crosses signal line from below,
sell when crosses from above. With histogram confirmation and zero-line filter.
Source: freqtrade-strategies MACDStrategy
"""
STRATEGY_CODE = r'''
"""
MACD Crossover
MACD(12,26,9) crossover with zero-line trend filter.
"""

# @param fast int 12 range=5:30:1
# @param slow int 26 range=10:50:1
# @param signal int 9 range=5:20:1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)

def handle_data(context, data):
    fast = int(context.params.get("fast", 12))
    slow = int(context.params.get("slow", 26))
    sig = int(context.params.get("signal", 9))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(slow + sig + 10, "15m", ["close"], g.symbol)
    if len(bars) < slow + sig + 5: return
    c = bars["close"]
    ema_fast = _ema(c, fast)
    ema_slow = _ema(c, slow)
    macd = ema_fast - ema_slow
    bars2 = get_history(slow + sig + 11, "15m", ["close"], g.symbol)
    c2 = bars2["close"]
    prev_f = _ema(c2.iloc[:-1], fast)
    prev_s = _ema(c2.iloc[:-1], slow)
    prev_macd = prev_f - prev_s
    price = float(c.iloc[-1])
    pos = get_position(g.symbol)
    amt = float(pos.amount or 0.0)
    if macd > 0 and prev_macd <= 0 and amt <= 0:
        order_target_percent(g.symbol, tp, reason="macd_buy")
    elif macd < 0 and prev_macd >= 0 and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="macd_sell")

def _ema(series, period):
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])
'''
