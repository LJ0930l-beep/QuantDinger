"""稳妥5: Triple EMA — conservative | crypto / us_stock | ~1h"""
STRATEGY_CODE = r'''
"""
稳妥5: Triple EMA (TEMA)
三重EMA平滑，滞后更小，适合中长线。
适用市场: 加密货币 / 美股
建议周期: 1h / 4h
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 1h
# @param tema_period int 9 range=5:30:1
# @param trend_ema int 50 range=20:200:10
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_universe([str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))])
    context.subscribe(frequency=str(context.params.get("frequency", "1h")))
    context.set_warmup(100)

def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    tp = int(context.params.get("tema_period", 9)); te = int(context.params.get("trend_ema", 50))
    target = float(context.params.get("target_pct", 0.9))
    bars = get_history(te + 30, str(context.params.get("frequency", "1h")), ["close"], symbol)
    if len(bars) < te + 5: return
    c = bars["close"]
    tema = _tema(c, tp); ema_trend = float(c.ewm(span=te, adjust=False).mean().iloc[-1])
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > tema and price > ema_trend and amt <= 0:
        order_target_percent(symbol, target, reason="s05_buy")
    elif price < tema and amt > 0:
        order_target_percent(symbol, 0.0, reason="s05_sell")

def _tema(series, period):
    e1 = series.ewm(span=period, adjust=False).mean()
    e2 = e1.ewm(span=period, adjust=False).mean()
    e3 = e2.ewm(span=period, adjust=False).mean()
    return float((3 * e1 - 3 * e2 + e3).iloc[-1])

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '1h'
RISK_LEVEL = 'conservative'
