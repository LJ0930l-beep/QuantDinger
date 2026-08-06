"""激进1: SuperTrend + ADX — aggressive | crypto / us_stock | ~15m"""
STRATEGY_CODE = r'''
"""
激进1: SuperTrend + ADX
趋势跟随+强度确认，ADX>25时开仓。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param st_period int 10 range=5:30:1
# @param st_mult float 3.0 range=1.0:5.0:0.5
# @param adx_threshold int 25 range=15:50:5
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    # Placeholder — runtime overrides from deployment config
    context.set_universe(["Crypto:BTC/USDT@spot"])
    context.subscribe(frequency="15m")
    context.set_warmup(100); g.st_prev = None

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    p = int(context.params.get("st_period", 10)); m = float(context.params.get("st_mult", 3.0))
    at = int(context.params.get("adx_threshold", 25)); tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(p * 4, freq, ["high", "low", "close"], symbol)
    if len(bars) < p * 3: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    st = _st(h, l, c, p, m); adx = _adx(h, l, c, 14)
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if g.st_prev is None: g.st_prev = st; return
    buy = g.st_prev <= price and st > price and adx > at
    sell = g.st_prev >= price and st < price
    g.st_prev = st
    if buy and amt <= 0: order_target_percent(symbol, tp, reason="a01_buy")
    elif sell and amt > 0: order_target_percent(symbol, 0.0, reason="a01_sell")

def _st(high, low, close, period, mult):
    n = len(close); st = [0] * n
    if n < period + 1: return 0
    tr = [max(float(high.iloc[i]) - float(low.iloc[i]), abs(float(high.iloc[i]) - float(close.iloc[i-1])), abs(float(low.iloc[i]) - float(close.iloc[i-1]))) for i in range(1, n)]
    atr = list(tr)
    for i in range(period, len(tr)):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    for i in range(period, n):
        hl2 = (float(high.iloc[i]) + float(low.iloc[i])) / 2
        up = hl2 + mult * atr[i-1]; lo = hl2 - mult * atr[i-1]
        if i == period:
            st[i] = lo if float(close.iloc[i]) <= lo else up
        else:
            prev = st[i-1]
            if prev == up and float(close.iloc[i]) <= lo:
                st[i] = lo
            elif prev == lo and float(close.iloc[i]) >= up:
                st[i] = up
            else:
                st[i] = prev
    return st[-1]

def _adx(high, low, close, period):
    n = len(close)
    if n < period + 1: return 0
    tr = []; pd = []; md = []
    for i in range(1, n):
        h = float(high.iloc[i]); l = float(low.iloc[i])
        tr.append(max(h - l, abs(h - float(close.iloc[i-1])), abs(l - float(close.iloc[i-1]))))
        up = h - float(high.iloc[i-1]); dn = float(low.iloc[i-1]) - l
        pd.append(up if up > dn and up > 0 else 0)
        md.append(dn if dn > up and dn > 0 else 0)
    atr = sum(tr[:period]) / period; sp = sum(pd[:period]) / period; sm = sum(md[:period]) / period
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
        sp = (sp * (period - 1) + pd[i]) / period; sm = (sm * (period - 1) + md[i]) / period
    return 100 * abs(sp - sm) / (sp + sm) if (sp + sm) > 0 else 0

'''
MARKET_SUITABLE = ['crypto', 'us_stock']
SUGGESTED_TIMEFRAME = '15m'
RISK_LEVEL = 'aggressive'
