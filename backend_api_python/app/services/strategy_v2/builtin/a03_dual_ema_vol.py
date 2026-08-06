"""激进3: Dual EMA + Volume — aggressive | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
激进3: Dual EMA + Volume
快慢EMA交叉 + 成交量放量确认。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param fast int 5 range=3:15:1
# @param slow int 20 range=10:50:5
# @param vol_factor float 1.5 range=1.0:3.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    # Placeholder — runtime overrides from deployment config
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100); g.prev_f = None; g.prev_s = None

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    fast = int(context.params.get("fast", 5)); slow = int(context.params.get("slow", 20))
    vf = float(context.params.get("vol_factor", 1.5)); tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(slow + 30, freq, ["close", "volume"], symbol)
    if len(bars) < slow + 5: return
    c = bars["close"]; v = bars["volume"]
    fe = float(c.ewm(span=fast, adjust=False).mean().iloc[-1])
    se = float(c.ewm(span=slow, adjust=False).mean().iloc[-1])
    av = float(v.tail(slow).mean()); cv = float(v.iloc[-1]); surge = cv > av * vf
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    buy = g.prev_f is not None and g.prev_f <= g.prev_s and fe > se and surge
    sell = g.prev_f is not None and g.prev_f >= g.prev_s and fe < se
    g.prev_f = fe; g.prev_s = se
    if buy and amt <= 0: order_target_percent(symbol, tp, reason="a03_buy")
    elif sell and amt > 0: order_target_percent(symbol, 0.0, reason="a03_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
