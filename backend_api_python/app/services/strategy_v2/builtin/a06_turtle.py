"""激进6: Turtle Trading — aggressive | crypto / us_stock | ~4h"""
STRATEGY_CODE = r'''
"""
激进6: Turtle Trading
海龟交易法则，Donchian通道突破。
适用市场: 加密货币 / 美股
建议周期: 4h / 1d
"""
# @param entry_window int 20 range=10:55:5
# @param exit_window int 10 range=5:30:5
# @param target_pct float 0.8 range=0.1:1:0.1

def initialize(context):
    # Placeholder — runtime overrides from deployment config
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "4h"
    ew = int(context.params.get("entry_window", 20)); xw = int(context.params.get("exit_window", 10))
    tp = float(context.params.get("target_pct", 0.8))
    bars = get_history(ew + 30, freq, ["high", "low", "close"], symbol)
    if len(bars) < ew + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    ehi = float(h.iloc[-ew:].max()); elo = float(l.iloc[-xw:].min())
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price >= ehi and amt <= 0: order_target_percent(symbol, tp, reason="a06_buy")
    elif price <= elo and amt > 0: order_target_percent(symbol, 0.0, reason="a06_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '4h'
RISK_LEVEL = 'aggressive'
