"""Triple EMA strategy."""
STRATEGY_CODE = r'''
"""
Triple EMA (TEMA)
TEMA(9) crossover with EMA(50) trend filter.
"""
# @param tema_period int 9 range=5:20:1
# @param trend_ema int 50 range=20:200:10
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)
    g.tema_prev = None

def handle_data(context, data):
    tp = int(context.params.get("tema_period", 9))
    te = int(context.params.get("trend_ema", 50))
    target = float(context.params.get("target_pct", 0.9))
    bars = get_history(te + 30, "15m", ["close"], g.symbol)
    if len(bars) < te + 5: return
    c = bars["close"]
    tema = _tema(c, tp); ema_trend = _ema(c, te)
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = price > tema and price > ema_trend
    sell = price < tema
    if buy and amt <= 0:
        order_target_percent(g.symbol, target, reason="tema_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="tema_sell")

def _ema(series, period):
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])

def _tema(series, period):
    e1 = series.ewm(span=period, adjust=False).mean()
    e2 = e1.ewm(span=period, adjust=False).mean()
    e3 = e2.ewm(span=period, adjust=False).mean()
    return float((3 * e1 - 3 * e2 + e3).iloc[-1])
'''
