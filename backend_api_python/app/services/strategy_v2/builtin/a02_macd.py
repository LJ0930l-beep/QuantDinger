"""激进2: MACD Crossover — aggressive | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
激进2: MACD Crossover
MACD(12,26,9)金叉死叉，零轴过滤。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param fast int 12 range=5:30:1
# @param slow int 26 range=10:50:1
# @param signal int 9 range=5:20:1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    # Placeholder — runtime overrides from deployment config
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    fast = int(context.params.get("fast", 12)); slow = int(context.params.get("slow", 26))
    sig = int(context.params.get("signal", 9)); tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(slow + sig + 11, freq, ["close"], symbol)
    if len(bars) < slow + sig + 5: return
    c = bars["close"]; c_prev = c.iloc[:-1]
    macd = float(c.ewm(span=fast, adjust=False).mean().iloc[-1]) - float(c.ewm(span=slow, adjust=False).mean().iloc[-1])
    pm = float(c_prev.ewm(span=fast, adjust=False).mean().iloc[-1]) - float(c_prev.ewm(span=slow, adjust=False).mean().iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if macd > 0 and pm <= 0 and amt <= 0:
        order_target_percent(symbol, tp, reason="a02_buy")
    elif macd < 0 and pm >= 0 and amt > 0:
        order_target_percent(symbol, 0.0, reason="a02_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
