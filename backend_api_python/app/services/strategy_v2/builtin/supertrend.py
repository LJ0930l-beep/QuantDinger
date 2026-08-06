"""SuperTrend + ADX strategy."""
STRATEGY_CODE = r'''
"""
SuperTrend + ADX
Multi-timeframe Supertrend with trend strength filter.
"""
# @param st_period int 10 range=5:30:1
# @param st_mult float 3.0 range=1.0:5.0:0.5
# @param adx_period int 14 range=7:30:1
# @param adx_threshold int 25 range=15:50:5
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)
    g.st_prev = None

def handle_data(context, data):
    p = int(context.params.get("st_period", 10))
    m = float(context.params.get("st_mult", 3.0))
    ap = int(context.params.get("adx_period", 14))
    at = int(context.params.get("adx_threshold", 25))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(max(p, ap) * 3 + 10, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < p * 3: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    st = _st(h, l, c, p, m)
    adx = _adx(h, l, c, ap)
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if g.st_prev is None: g.st_prev = st; return
    buy = g.st_prev <= price and st > price and adx > at
    sell = g.st_prev >= price and st < price
    g.st_prev = st
    if buy and amt <= 0:
        order_target_percent(g.symbol, tp, reason="st_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="st_sell")

def _st(high, low, close, period, mult):
    n = len(close)
    if n < period + 1: return 0
    tr = []; atr_vals = []; st = [0] * n
    for i in range(1, n):
        tr.append(max(float(high.iloc[i]) - float(low.iloc[i]), abs(float(high.iloc[i]) - float(close.iloc[i-1])), abs(float(low.iloc[i]) - float(close.iloc[i-1]))))
    atr_vals = list(tr)
    for i in range(period, len(tr)):
        atr_vals[i] = (atr_vals[i-1] * (period - 1) + tr[i]) / period
    for i in range(period, n):
        hl2 = (float(high.iloc[i]) + float(low.iloc[i])) / 2
        upper = hl2 + mult * atr_vals[i-1]
        lower = hl2 - mult * atr_vals[i-1]
        if i == period:
            st[i] = lower if float(close.iloc[i]) <= lower else upper
        else:
            st[i] = lower if (st[i-1] == upper and float(close.iloc[i]) <= lower) else (upper if (st[i-1] == lower and float(close.iloc[i]) >= upper) else (lower if st[i-1] == upper else st[i-1]))
    return st[-1]

def _adx(high, low, close, period):
    n = len(close)
    if n < period + 1: return 0
    tr = []; pd = []; md = []
    for i in range(1, n):
        h = float(high.iloc[i]); l = float(low.iloc[i])
        ph = float(high.iloc[i-1]); pl = float(low.iloc[i-1])
        pc = float(close.iloc[i-1])
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        up = h - ph; dn = pl - l
        pd.append(up if up > dn and up > 0 else 0)
        md.append(dn if dn > up and dn > 0 else 0)
    atr = sum(tr[:period]) / period
    sp = sum(pd[:period]) / period; sm = sum(md[:period]) / period
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
        sp = (sp * (period - 1) + pd[i]) / period
        sm = (sm * (period - 1) + md[i]) / period
    pdi = 100 * sp / atr if atr > 0 else 0; mdi = 100 * sm / atr if atr > 0 else 0
    return 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
'''
