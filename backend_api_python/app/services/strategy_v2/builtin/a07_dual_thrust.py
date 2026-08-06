"""激进7: Dual Thrust — aggressive | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
激进7: Dual Thrust Breakout
经典区间突破，基于前N根K线的高低点。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param lookback int 4 range=2:20:1
# @param k1 float 0.5 range=0.3:1.0:0.1
# @param k2 float 0.5 range=0.3:1.0:0.1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    # Placeholder — runtime overrides from deployment config
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    lb = int(context.params.get("lookback", 4))
    k1 = float(context.params.get("k1", 0.5)); k2 = float(context.params.get("k2", 0.5))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(lb + 10, freq, ["high", "low", "close", "open"], symbol)
    if len(bars) < lb + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]; o = bars["open"]
    hh = float(h.iloc[-lb-1:-1].max()); ll = float(l.iloc[-lb-1:-1].min())
    hc = float(c.iloc[-lb-1:-1].max()); lc = float(c.iloc[-lb-1:-1].min())
    rng = max(hh - lc, hc - ll); op = float(o.iloc[-1])
    upper = op + k1 * rng; lower = op - k2 * rng; price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price >= upper and amt <= 0: order_target_percent(symbol, tp, reason="a07_buy")
    elif price <= lower and amt > 0: order_target_percent(symbol, 0.0, reason="a07_sell")

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
