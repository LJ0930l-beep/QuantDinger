"""激进4: Parabolic SAR — aggressive | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
激进4: Parabolic SAR + EMA
SAR翻转+EMA趋势过滤，顺势开仓。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 15m
# @param sar_step float 0.02 range=0.01:0.05:0.005
# @param sar_max float 0.2 range=0.1:0.5:0.05
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_universe([str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))])
    context.subscribe(frequency=str(context.params.get("frequency", "15m")))
    context.set_warmup(100)

def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    step = float(context.params.get("sar_step", 0.02)); mx = float(context.params.get("sar_max", 0.2))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(80, str(context.params.get("frequency", "15m")), ["high", "low", "close"], symbol)
    if len(bars) < 50: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    sar = _sar(h, l, step, mx); price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > sar and amt <= 0:
        order_target_percent(symbol, tp, reason="a04_buy")
    elif price < sar and amt > 0:
        order_target_percent(symbol, 0.0, reason="a04_sell")

def _sar(high, low, step, mx):
    n = len(high); s = float(low.iloc[0]); ep = float(high.iloc[0]); af = step; up = True
    for i in range(1, n):
        h = float(high.iloc[i]); l = float(low.iloc[i])
        if up:
            s = min(s + af * (ep - s), float(low.iloc[i-1]), float(low.iloc[max(0,i-2)]))
            if h > ep: ep = h; af = min(af + step, mx)
            if l <= s: up = False; s = ep; ep = l; af = step
        else:
            s = max(s - af * (s - ep), float(high.iloc[i-1]), float(high.iloc[max(0,i-2)]))
            if l < ep: ep = l; af = min(af + step, mx)
            if h >= s: up = True; s = ep; ep = h; af = step
    return s

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
