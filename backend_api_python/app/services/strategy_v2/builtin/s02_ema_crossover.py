"""稳妥2: EMA Crossover — conservative | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
稳妥2: EMA 8/21 Crossover
经典趋势跟踪，快慢EMA交叉 + 长周期趋势过滤。
适用市场: 加密货币 / 美股
建议周期: 15m / 4h
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 15m
# @param fast int 8 range=3:20:1
# @param slow int 21 range=10:50:1
# @param filter_period int 200 range=50:300:25
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)
def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    freq = str(context.params.get("frequency", "15m"))
    fast = int(context.params.get("fast", 8)); slow = int(context.params.get("slow", 21))
    ft = int(context.params.get("filter_period", 200))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(ft + 10, freq, ["close"], symbol)
    if len(bars) < ft + 5: return
    c = bars["close"]
    fe = float(c.ewm(span=fast, adjust=False).mean().iloc[-1])
    se = float(c.ewm(span=slow, adjust=False).mean().iloc[-1])
    te = float(c.ewm(span=ft, adjust=False).mean().iloc[-1])
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if fe > se and price > te and amt <= 0:
        order_target_percent(symbol, tp, reason="s02_buy")
    elif fe < se and amt > 0:
        order_target_percent(symbol, 0.0, reason="s02_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'conservative'
