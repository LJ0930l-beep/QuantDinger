"""Write 7 strategy DSL files."""
import os

d = os.path.dirname(os.path.abspath(__file__))

strategies = {
    'supertrend.py': '''"""SuperTrend + ADX strategy."""
STRATEGY_CODE = r\'\'\'
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
\'\'\'
''',
    'ichimoku.py': '''"""Ichimoku Cloud strategy."""
STRATEGY_CODE = r\'\'\'
"""
Ichimoku Cloud
Tenkan-sen / Kijun-sen cross + price vs cloud.
"""
# @param tenkan int 9 range=5:30:1
# @param kijun int 26 range=10:60:1
# @param senkou_b int 52 range=20:100:5
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(150)

def handle_data(context, data):
    t = int(context.params.get("tenkan", 9))
    k = int(context.params.get("kijun", 26))
    sb = int(context.params.get("senkou_b", 52))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(sb + 30, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < sb + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    tenken = (float(h.iloc[-t:].max()) + float(l.iloc[-t:].min())) / 2
    kijun = (float(h.iloc[-k:].max()) + float(l.iloc[-k:].min())) / 2
    sa = (float(h.iloc[-t:].max()) + float(l.iloc[-t:].min()) + float(h.iloc[-k:].max()) + float(l.iloc[-k:].min())) / 4
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = tenken > kijun and price > sa
    sell = tenken < kijun
    if buy and amt <= 0:
        order_target_percent(g.symbol, tp, reason="ichimoku_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="ichimoku_sell")
\'\'\'
''',
    'dual_ema_volume.py': '''"""Dual EMA + Volume strategy."""
STRATEGY_CODE = r\'\'\'
"""
Dual EMA + Volume Surge
EMA(5,20) crossover with volume confirmation.
"""
# @param fast int 5 range=3:15:1
# @param slow int 20 range=10:50:5
# @param vol_factor float 1.5 range=1.0:3.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)
    g.prev_fast = None; g.prev_slow = None

def handle_data(context, data):
    fast = int(context.params.get("fast", 5))
    slow = int(context.params.get("slow", 20))
    vf = float(context.params.get("vol_factor", 1.5))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(slow + 30, "15m", ["close", "volume"], g.symbol)
    if len(bars) < slow + 5: return
    c = bars["close"]; v = bars["volume"]
    f_ema = _ema(c, fast); s_ema = _ema(c, slow)
    avg_vol = float(v.tail(slow).mean())
    cur_vol = float(v.iloc[-1])
    surge = cur_vol > avg_vol * vf
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = g.prev_fast is not None and g.prev_fast <= g.prev_slow and f_ema > s_ema and surge
    sell = g.prev_fast is not None and g.prev_fast >= g.prev_slow and f_ema < s_ema
    g.prev_fast = f_ema; g.prev_slow = s_ema
    if buy and amt <= 0:
        order_target_percent(g.symbol, tp, reason="ema_vol_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="ema_vol_sell")

def _ema(series, period):
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])
\'\'\'
''',
    'parabolic_sar.py': '''"""Parabolic SAR strategy."""
STRATEGY_CODE = r\'\'\'
"""
Parabolic SAR + EMA Filter
Trend following with SAR flip and EMA(50) trend filter.
"""
# @param sar_step float 0.02 range=0.01:0.05:0.005
# @param sar_max float 0.2 range=0.1:0.5:0.05
# @param ema_filter int 50 range=20:200:10
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)
    g.sar_prev = None

def handle_data(context, data):
    step = float(context.params.get("sar_step", 0.02))
    mx = float(context.params.get("sar_max", 0.2))
    ema_p = int(context.params.get("ema_filter", 50))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(ema_p + 30, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < ema_p + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    ema50 = float(c.tail(ema_p).mean())
    sar = _sar(h, l, step, mx)
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = price > sar and price > ema50
    sell = price < sar
    if buy and amt <= 0:
        order_target_percent(g.symbol, tp, reason="sar_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="sar_sell")

def _sar(high, low, step, mx):
    n = len(high); sar = float(low.iloc[0]); ep = float(high.iloc[0])
    af = step; uptrend = True
    for i in range(1, n):
        h = float(high.iloc[i]); l = float(low.iloc[i])
        if uptrend:
            sar = min(sar + af * (ep - sar), float(low.iloc[i-1]), float(low.iloc[max(0,i-2)]))
            if h > ep: ep = h; af = min(af + step, mx)
            if l <= sar: uptrend = False; sar = ep; ep = l; af = step
        else:
            sar = max(sar - af * (sar - ep), float(high.iloc[i-1]), float(high.iloc[max(0,i-2)]))
            if l < ep: ep = l; af = min(af + step, mx)
            if h >= sar: uptrend = True; sar = ep; ep = h; af = step
    return sar
\'\'\'
''',
    'keltner_breakout.py': '''"""Keltner Channel Breakout strategy."""
STRATEGY_CODE = r\'\'\'
"""
Keltner Channel Breakout
Volatility expansion breakout strategy.
"""
# @param kc_period int 20 range=10:50:5
# @param kc_mult float 2.0 range=1.0:4.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)

def handle_data(context, data):
    p = int(context.params.get("kc_period", 20))
    m = float(context.params.get("kc_mult", 2.0))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(p + 20, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < p + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    typ = [(float(h.iloc[i]) + float(l.iloc[i]) + float(c.iloc[i])) / 3 for i in range(len(c))]
    sma = sum(typ[-p:]) / p
    tr = [max(float(h.iloc[i]) - float(l.iloc[i]), abs(float(h.iloc[i]) - float(c.iloc[i-1])), abs(float(l.iloc[i]) - float(c.iloc[i-1]))) for i in range(1, len(c))]
    atr = sum(tr[-p:]) / p
    upper = sma + m * atr; lower = sma - m * atr
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if price > upper and amt <= 0:
        order_target_percent(g.symbol, tp, reason="kc_breakout_buy")
    elif price < lower and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="kc_breakout_sell")
\'\'\'
''',
    'rsi_scalper.py': '''"""RSI Scalper 5m strategy."""
STRATEGY_CODE = r\'\'\'
"""
RSI Scalper 5m
Pure RSI mean reversion: buy oversold, sell overbought.
"""
# @param rsi_period int 14 range=7:30:1
# @param rsi_low int 25 range=15:35:5
# @param rsi_high int 75 range=65:85:5
# @param target_pct float 1.0 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="5m")
    context.set_warmup(50)

def handle_data(context, data):
    rp = int(context.params.get("rsi_period", 14))
    rl = int(context.params.get("rsi_low", 25))
    rh = int(context.params.get("rsi_high", 75))
    tp = float(context.params.get("target_pct", 1.0))
    bars = get_history(rp + 10, "5m", ["close"], g.symbol)
    if len(bars) < rp + 5: return
    c = bars["close"]
    rsi = _rsi(c, rp)
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if rsi < rl and amt <= 0:
        order_target_percent(g.symbol, tp, reason="rsi_scalp_buy")
    elif rsi > rh and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="rsi_scalp_sell")

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
\'\'\'
''',
    'turtle_trading.py': '''"""Turtle Trading strategy."""
STRATEGY_CODE = r\'\'\'
"""
Turtle Trading
Classic Donchian channel breakout.
"""
# @param entry_window int 20 range=10:55:5
# @param exit_window int 10 range=5:30:5
# @param target_pct float 0.8 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(80)

def handle_data(context, data):
    ew = int(context.params.get("entry_window", 20))
    xw = int(context.params.get("exit_window", 10))
    tp = float(context.params.get("target_pct", 0.8))
    bars = get_history(ew + 30, "15m", ["high", "low", "close"], g.symbol)
    if len(bars) < ew + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    entry_high = float(h.iloc[-ew:].max())
    exit_low = float(l.iloc[-xw:].min())
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if price >= entry_high and amt <= 0:
        order_target_percent(g.symbol, tp, reason="turtle_buy")
    elif price <= exit_low and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="turtle_sell")
\'\'\'
''',
    'triple_ema.py': '''"""Triple EMA strategy."""
STRATEGY_CODE = r\'\'\'
"""
Triple EMA (TEMA)
TEMA(9) crossover with EMA(50) trend filter.
"""
# @param tema_period int 9 range=5:20:1
# @param trend_ema int 50 range=20:200:10
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(100)
    g.tema_prev = None

def handle_data(context, data):
    tp = int(context.params.get("tema_period", 9))
    te = int(context.params.get("trend_ema", 50))
    target = float(context.params.get("target_pct", 0.9))
    bars = get_history(te + 30, "15m", ["close"], g.symbol)
    if len(bars) < te + 5: return
    c = bars["close"]
    tema = _tema(c, tp); ema_trend = _ema(c, te)
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    buy = price > tema and price > ema_trend
    sell = price < tema
    if buy and amt <= 0:
        order_target_percent(g.symbol, target, reason="tema_buy")
    elif sell and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="tema_sell")

def _ema(series, period):
    return float(series.ewm(span=period, adjust=False).mean().iloc[-1])

def _tema(series, period):
    e1 = series.ewm(span=period, adjust=False).mean()
    e2 = e1.ewm(span=period, adjust=False).mean()
    e3 = e2.ewm(span=period, adjust=False).mean()
    return float((3 * e1 - 3 * e2 + e3).iloc[-1])
\'\'\'
''',
    'dual_thrust.py': '''"""Dual Thrust breakout strategy."""
STRATEGY_CODE = r\'\'\'
"""
Dual Thrust Breakout
Classic range breakout system.
"""
# @param lookback int 4 range=2:20:1
# @param k1 float 0.5 range=0.3:1.0:0.1
# @param k2 float 0.5 range=0.3:1.0:0.1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m")
    context.set_warmup(30)

def handle_data(context, data):
    lb = int(context.params.get("lookback", 4))
    k1 = float(context.params.get("k1", 0.5))
    k2 = float(context.params.get("k2", 0.5))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(lb + 10, "15m", ["high", "low", "close", "open"], g.symbol)
    if len(bars) < lb + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]; o = bars["open"]
    hh = float(h.iloc[-lb-1:-1].max()); ll = float(l.iloc[-lb-1:-1].min())
    hc = float(c.iloc[-lb-1:-1].max()); lc = float(c.iloc[-lb-1:-1].min())
    rng = max(hh - lc, hc - ll)
    op = float(o.iloc[-1])
    upper = op + k1 * rng; lower = op - k2 * rng
    price = float(c.iloc[-1])
    pos = get_position(g.symbol); amt = float(pos.amount or 0.0)
    if price >= upper and amt <= 0:
        order_target_percent(g.symbol, tp, reason="dual_thrust_buy")
    elif price <= lower and amt > 0:
        order_target_percent(g.symbol, 0.0, reason="dual_thrust_sell")
\'\'\'
''',
}

for name, content in strategies.items():
    path = os.path.join(d, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'Created {name}')

print(f'Done: {len(strategies)} files')
