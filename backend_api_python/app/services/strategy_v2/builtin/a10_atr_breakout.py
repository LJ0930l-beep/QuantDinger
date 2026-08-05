"""激进10: ATR Channel Breakout — aggressive | crypto | ~15m"""
STRATEGY_CODE = r'''
"""
激进10: ATR Channel Breakout
ATR动态通道突破，波动率自适应。
适用市场: 加密货币
建议周期: 15m / 1h
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 15m
# @param atr_period int 14 range=7:30:1
# @param atr_mult float 2.0 range=1.0:4.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_universe([str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))])
    context.subscribe(frequency=str(context.params.get("frequency", "15m")))
    context.set_warmup(50)

def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    ap = int(context.params.get("atr_period", 14)); am = float(context.params.get("atr_mult", 2.0))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(ap + 30, str(context.params.get("frequency", "15m")), ["high", "low", "close"], symbol)
    if len(bars) < ap + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    tr = [max(float(h.iloc[i]) - float(l.iloc[i]), abs(float(h.iloc[i]) - float(c.iloc[i-1])), abs(float(l.iloc[i]) - float(c.iloc[i-1]))) for i in range(1, len(c))]
    atr = sum(tr[-ap:]) / ap; sma = float(c.tail(ap).mean())
    upper = sma + am * atr; lower = sma - am * atr; price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > upper and amt <= 0: order_target_percent(symbol, tp, reason="a10_buy")
    elif price < lower and amt > 0: order_target_percent(symbol, 0.0, reason="a10_sell")

'''
MARKET_SUITABLE = ['crypto']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
