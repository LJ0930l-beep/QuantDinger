"""激进2: MACD Crossover — aggressive | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
激进2: MACD Crossover
MACD(12,26,9)金叉死叉，零轴过滤。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 15m
# @param fast int 12 range=5:30:1
# @param slow int 26 range=10:50:1
# @param signal int 9 range=5:20:1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_universe([str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))])
    context.subscribe(frequency=str(context.params.get("frequency", "15m")))
    context.set_warmup(100)

def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    fast = int(context.params.get("fast", 12)); slow = int(context.params.get("slow", 26))
    sig = int(context.params.get("signal", 9)); tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(slow + sig + 10, str(context.params.get("frequency", "15m")), ["close"], symbol)
    if len(bars) < slow + sig + 5: return
    c = bars["close"]
    macd = float(c.ewm(span=fast, adjust=False).mean().iloc[-1]) - float(c.ewm(span=slow, adjust=False).mean().iloc[-1])
    bars2 = get_history(slow + sig + 11, str(context.params.get("frequency", "15m")), ["close"], symbol)
    c2 = bars2["close"]
    pm = float(c2.iloc[:-1].ewm(span=fast, adjust=False).mean().iloc[-1]) - float(c2.iloc[:-1].ewm(span=slow, adjust=False).mean().iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if macd > 0 and pm <= 0 and amt <= 0:
        order_target_percent(symbol, tp, reason="a02_buy")
    elif macd < 0 and pm >= 0 and amt > 0:
        order_target_percent(symbol, 0.0, reason="a02_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
