"""稳妥3: RSI Scalper — conservative | crypto / us_stock | ~5m"""
STRATEGY_CODE = r'''
"""
稳妥3: RSI 超卖反弹
纯RSI均值回归，严格超卖买入超卖卖出。
适用市场: 加密货币 / 美股
建议周期: 5m / 15m
"""
# @param symbol str Crypto:BTC/USDT@spot
# @param frequency str 5m
# @param rsi_period int 14 range=7:30:1
# @param rsi_low int 25 range=15:35:5
# @param rsi_high int 75 range=65:85:5
# @param target_pct float 1.0 range=0.1:1:0.1

def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="5m")
    context.set_warmup(100)
def handle_data(context, data):
    symbol = str(context.params.get("symbol", "Crypto:BTC/USDT@spot"))
    rp = int(context.params.get("rsi_period", 14)); rl = int(context.params.get("rsi_low", 25))
    rh = int(context.params.get("rsi_high", 75)); tp = float(context.params.get("target_pct", 1.0))
    bars = get_history(rp + 10, str(context.params.get("frequency", "5m")), ["close"], symbol)
    if len(bars) < rp + 5: return
    c = bars["close"]; rsi = _rsi(c, rp)
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if rsi < rl and amt <= 0:
        order_target_percent(symbol, tp, reason="s03_buy")
    elif rsi > rh and amt > 0:
        order_target_percent(symbol, 0.0, reason="s03_sell")

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
SUGGESTED_TIMEFRAME = '5m'
RISK_LEVEL = 'conservative'
