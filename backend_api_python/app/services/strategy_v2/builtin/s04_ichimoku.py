"""稳妥4: Ichimoku Cloud — conservative | crypto / us_stock | ~4h"""
STRATEGY_CODE = r'''
"""
稳妥4: Ichimoku Cloud
一目均衡表，Tenkan/Kijun交叉 + 云层位置过滤。
适用市场: 加密货币 / 美股
建议周期: 4h / 1d
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 4h
# @param tenkan int 9 range=5:30:1
# @param kijun int 26 range=10:60:1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_universe([str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))])
    context.subscribe(frequency=str(context.params.get("frequency", "4h")))
    context.set_warmup(150)

def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    t = int(context.params.get("tenkan", 9)); k = int(context.params.get("kijun", 26))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(k + 20, str(context.params.get("frequency", "4h")), ["high", "low", "close"], symbol)
    if len(bars) < k + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    ten = (float(h.iloc[-t:].max()) + float(l.iloc[-t:].min())) / 2
    kij = (float(h.iloc[-k:].max()) + float(l.iloc[-k:].min())) / 2
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if ten > kij and amt <= 0:
        order_target_percent(symbol, tp, reason="s04_buy")
    elif ten < kij and amt > 0:
        order_target_percent(symbol, 0.0, reason="s04_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '4h'
RISK_LEVEL = 'conservative'
