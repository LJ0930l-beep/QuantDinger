"""稳妥1: Bollinger Bands + RSI — conservative | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
稳妥1: Bollinger Bands + RSI
均值回归策略，布林带下轨超卖买入，上轨超买卖出。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param symbol str Crypto:BTC/USDT@spot 标的
# @param frequency str 15m 周期
# @param bb_window int 20 range=10:50:5
# @param bb_std float 2.0 range=1.0:3.0:0.25
# @param rsi_period int 14 range=7:30:1
# @param rsi_low int 30 range=20:40:5
# @param rsi_high int 70 range=60:80:5
# @param target_pct float 0.95 range=0.1:1:0.1

def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100)
def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    freq = str(context.params.get("frequency", "15m"))
    w = int(context.params.get("bb_window", 20))
    std = float(context.params.get("bb_std", 2.0))
    rp = int(context.params.get("rsi_period", 14))
    rl = int(context.params.get("rsi_low", 30))
    rh = int(context.params.get("rsi_high", 70))
    tp = float(context.params.get("target_pct", 0.95))
    bars = get_history(max(w, rp) + 10, freq, ["close"], symbol)
    if len(bars) < w + 5: return
    c = bars["close"]
    sma = float(c.tail(w).mean()); st = float(c.tail(w).std())
    lower = sma - std * st; upper = sma + std * st
    price = float(c.iloc[-1]); rsi = _rsi(c, rp)
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price <= lower and rsi < rl and amt <= 0:
        order_target_percent(symbol, tp, reason="s01_buy")
    elif (price >= upper or rsi > rh) and amt > 0:
        order_target_percent(symbol, 0.0, reason="s01_sell")

def _rsi(close, p):
    n = len(close)
    if n < p + 1: return 50.0
    g = []; l = []
    for i in range(1, n):
        d = float(close.iloc[i]) - float(close.iloc[i-1])
        g.append(d if d > 0 else 0.0); l.append(-d if d < 0 else 0.0)
    ag = sum(g[:p]) / p; al = sum(l[:p]) / p
    for i in range(p, len(g)):
        ag = (ag * (p - 1) + g[i]) / p; al = (al * (p - 1) + l[i]) / p
    return 100.0 - (100.0 / (1.0 + ag / al)) if al > 0 else 100.0

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'conservative'
