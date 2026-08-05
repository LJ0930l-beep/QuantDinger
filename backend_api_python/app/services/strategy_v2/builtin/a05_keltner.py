"""激进5: Keltner Breakout — aggressive | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
激进5: Keltner Channel Breakout
波动率扩张突破，ATR动态通道。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 15m
# @param kc_period int 20 range=10:50:5
# @param kc_mult float 2.0 range=1.0:4.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)
def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    p = int(context.params.get("kc_period", 20)); m = float(context.params.get("kc_mult", 2.0))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(p + 20, str(context.params.get("frequency", "15m")), ["high", "low", "close"], symbol)
    if len(bars) < p + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    typ = [(float(h.iloc[i]) + float(l.iloc[i]) + float(c.iloc[i])) / 3 for i in range(len(c))]
    sma = sum(typ[-p:]) / p
    tr = [max(float(h.iloc[i]) - float(l.iloc[i]), abs(float(h.iloc[i]) - float(c.iloc[i-1])), abs(float(l.iloc[i]) - float(c.iloc[i-1]))) for i in range(1, len(c))]
    atr = sum(tr[-p:]) / p
    upper = sma + m * atr; lower = sma - m * atr; price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > upper and amt <= 0: order_target_percent(symbol, tp, reason="a05_buy")
    elif price < lower and amt > 0: order_target_percent(symbol, 0.0, reason="a05_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
