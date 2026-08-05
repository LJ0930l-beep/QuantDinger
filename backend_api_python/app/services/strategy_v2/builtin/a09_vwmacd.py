"""激进9: Volume-Weighted MACD — aggressive | crypto | ~15m"""
STRATEGY_CODE = r'''
"""
激进9: VWAP + MACD
成交量加权MACD，放量金叉开仓。
适用市场: 加密货币
建议周期: 15m / 1h
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 15m
# @param fast int 12 range=5:30:1
# @param slow int 26 range=10:50:1
# @param vol_ratio float 1.2 range=1.0:2.0:0.1
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_universe([str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))])
    context.subscribe(frequency=str(context.params.get("frequency", "15m")))
    context.set_warmup(100)

def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    fast = int(context.params.get("fast", 12)); slow = int(context.params.get("slow", 26))
    vr = float(context.params.get("vol_ratio", 1.2)); tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(slow + 30, str(context.params.get("frequency", "15m")), ["close", "volume"], symbol)
    if len(bars) < slow + 5: return
    c = bars["close"]; v = bars["volume"]
    vw = [(float(c.iloc[i]) * float(v.iloc[i])) for i in range(len(c))]
    vw_fast = sum(vw[-fast:]) / sum([float(v.iloc[i]) for i in range(-fast, 0)]) if sum([float(v.iloc[i]) for i in range(-fast, 0)]) > 0 else 0
    vw_slow = sum(vw[-slow:]) / sum([float(v.iloc[i]) for i in range(-slow, 0)]) if sum([float(v.iloc[i]) for i in range(-slow, 0)]) > 0 else 0
    macd = vw_fast - vw_slow
    av = float(v.tail(20).mean()); cv = float(v.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if macd > 0 and cv > av * vr and amt <= 0:
        order_target_percent(symbol, tp, reason="a09_buy")
    elif macd < 0 and amt > 0:
        order_target_percent(symbol, 0.0, reason="a09_sell")

'''
MARKET_SUITABLE = ['crypto']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
