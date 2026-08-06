"""激进8: Donchian Breakout — aggressive | crypto / us_stock | ~4h"""
STRATEGY_CODE = r'''
"""
激进8: Donchian Channel Breakout
价格突破N日高低点，顺势跟踪。
适用市场: 加密货币 / 美股
建议周期: 4h / 1d
"""
# @param channel int 55 range=20:100:5
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    # Placeholder — runtime overrides from deployment config
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "4h"
    ch = int(context.params.get("channel", 55)); tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(ch + 20, freq, ["high", "low", "close"], symbol)
    if len(bars) < ch + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    upper = float(h.iloc[-ch:].max()); lower = float(l.iloc[-ch:].min())
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > upper and amt <= 0: order_target_percent(symbol, tp, reason="a08_buy")
    elif price < lower and amt > 0: order_target_percent(symbol, 0.0, reason="a08_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '4h'
RISK_LEVEL = 'aggressive'
