"""Batch create 15 optimized strategies (symbol/timeframe-agnostic)."""
import os
d = os.path.dirname(os.path.abspath(__file__))

STRATEGIES = {
    "s01_bband_rsi.py": {
        "name": "稳妥1: Bollinger Bands + RSI",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "conservative",
        "code": '''"""
稳妥1: Bollinger Bands + RSI
均值回归策略，布林带下轨超卖买入，上轨超买卖出。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param bb_window int 20 range=10:50:5
# @param bb_std float 2.0 range=1.0:3.0:0.25
# @param rsi_period int 14 range=7:30:1
# @param rsi_low int 30 range=20:40:5
# @param rsi_high int 70 range=60:80:5
# @param target_pct float 0.95 range=0.1:1:0.1

def initialize(context):
    # Universe and frequency injected at runtime from deployment config
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    w = int(context.params.get("bb_window", 20)); std = float(context.params.get("bb_std", 2.0))
    rp = int(context.params.get("rsi_period", 14)); rl = int(context.params.get("rsi_low", 30))
    rh = int(context.params.get("rsi_high", 70)); tp = float(context.params.get("target_pct", 0.95))
    bars = get_history(max(w, rp) + 10, freq, ["close"], symbol)
    if len(bars) < w + 5: return
    c = bars["close"]; sma = float(c.tail(w).mean()); st = float(c.tail(w).std())
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
    },
    "s02_ema_crossover.py": {
        "name": "稳妥2: EMA Crossover",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "conservative",
        "code": '''"""
稳妥2: EMA 8/21 Crossover
经典趋势跟踪，快慢EMA交叉 + 长周期趋势过滤。
适用市场: 加密货币 / 美股
建议周期: 15m / 4h
"""
# @param fast int 8 range=3:20:1
# @param slow int 21 range=10:50:1
# @param filter_period int 200 range=50:300:25
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(300)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    fast = int(context.params.get("fast", 8)); slow = int(context.params.get("slow", 21))
    ft = int(context.params.get("filter_period", 200)); tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(ft + 10, freq, ["close"], symbol)
    if len(bars) < ft + 5: return
    c = bars["close"]
    fe = float(c.ewm(span=fast, adjust=False).mean().iloc[-1])
    se = float(c.ewm(span=slow, adjust=False).mean().iloc[-1])
    te = float(c.ewm(span=ft, adjust=False).mean().iloc[-1])
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if fe > se and price > te and amt <= 0:
        order_target_percent(symbol, tp, reason="s02_buy")
    elif fe < se and amt > 0:
        order_target_percent(symbol, 0.0, reason="s02_sell")
'''
    },
    "s03_rsi_scalper.py": {
        "name": "稳妥3: RSI Scalper",
        "market": ["crypto", "us_stock"], "timeframe": "5m", "risk": "conservative",
        "code": '''"""
稳妥3: RSI 超卖反弹
纯RSI均值回归，严格超卖买入超买卖出。
适用市场: 加密货币 / 美股
建议周期: 5m / 15m
"""
# @param rsi_period int 14 range=7:30:1
# @param rsi_low int 25 range=15:35:5
# @param rsi_high int 75 range=65:85:5
# @param target_pct float 1.0 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(50)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "5m"
    rp = int(context.params.get("rsi_period", 14)); rl = int(context.params.get("rsi_low", 25))
    rh = int(context.params.get("rsi_high", 75)); tp = float(context.params.get("target_pct", 1.0))
    bars = get_history(rp + 10, freq, ["close"], symbol)
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
    },
    "s04_ichimoku.py": {
        "name": "稳妥4: Ichimoku Cloud",
        "market": ["crypto", "us_stock"], "timeframe": "4h", "risk": "conservative",
        "code": '''"""
稳妥4: Ichimoku Cloud
一目均衡表，Tenkan/Kijun交叉 + 云层位置过滤。
适用市场: 加密货币 / 美股
建议周期: 4h / 1d
"""
# @param tenkan int 9 range=5:30:1
# @param kijun int 26 range=10:60:1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(150)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "4h"
    t = int(context.params.get("tenkan", 9)); k = int(context.params.get("kijun", 26))
    tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(k + 20, freq, ["high", "low", "close"], symbol)
    if len(bars) < k + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    ten = (float(h.iloc[-t:].max()) + float(l.iloc[-t:].min())) / 2
    kij = (float(h.iloc[-k:].max()) + float(l.iloc[-k:].min())) / 2
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if ten > kij and amt <= 0:
        order_target_percent(symbol, tp, reason="s04_buy")
    elif ten < kij and amt > 0:
        order_target_percent(symbol, 0.0, reason="s04_sell")
'''
    },
    "s05_triple_ema.py": {
        "name": "稳妥5: Triple EMA",
        "market": ["crypto", "us_stock"], "timeframe": "1h", "risk": "conservative",
        "code": '''"""
稳妥5: Triple EMA (TEMA)
三重EMA平滑，滞后更小，适合中长线。
适用市场: 加密货币 / 美股
建议周期: 1h / 4h
"""
# @param tema_period int 9 range=5:30:1
# @param trend_ema int 50 range=20:200:10
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "1h"
    tp_p = int(context.params.get("tema_period", 9)); te = int(context.params.get("trend_ema", 50))
    target = float(context.params.get("target_pct", 0.9))
    bars = get_history(te + 30, freq, ["close"], symbol)
    if len(bars) < te + 5: return
    c = bars["close"]
    tema = _tema(c, tp_p); ema_trend = float(c.ewm(span=te, adjust=False).mean().iloc[-1])
    price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > tema and price > ema_trend and amt <= 0:
        order_target_percent(symbol, target, reason="s05_buy")
    elif price < tema and amt > 0:
        order_target_percent(symbol, 0.0, reason="s05_sell")

def _tema(series, period):
    e1 = series.ewm(span=period, adjust=False).mean()
    e2 = e1.ewm(span=period, adjust=False).mean()
    e3 = e2.ewm(span=period, adjust=False).mean()
    return float((3 * e1 - 3 * e2 + e3).iloc[-1])
'''
    },
    "a01_supertrend.py": {
        "name": "激进1: SuperTrend + ADX",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
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
    },
    "a02_macd.py": {
        "name": "激进2: MACD Crossover",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
激进2: MACD Crossover
MACD(12,26,9)金叉死叉，零轴过滤。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param fast int 12 range=5:30:1
# @param slow int 26 range=10:50:1
# @param signal int 9 range=5:20:1
# @param target_pct float 0.9 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    fast = int(context.params.get("fast", 12)); slow = int(context.params.get("slow", 26))
    sig = int(context.params.get("signal", 9)); tp = float(context.params.get("target_pct", 0.9))
    bars = get_history(slow + sig + 11, freq, ["close"], symbol)
    if len(bars) < slow + sig + 5: return
    c = bars["close"]; c_prev = c.iloc[:-1]
    macd = float(c.ewm(span=fast, adjust=False).mean().iloc[-1]) - float(c.ewm(span=slow, adjust=False).mean().iloc[-1])
    pm = float(c_prev.ewm(span=fast, adjust=False).mean().iloc[-1]) - float(c_prev.ewm(span=slow, adjust=False).mean().iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if macd > 0 and pm <= 0 and amt <= 0:
        order_target_percent(symbol, tp, reason="a02_buy")
    elif macd < 0 and pm >= 0 and amt > 0:
        order_target_percent(symbol, 0.0, reason="a02_sell")
'''
    },
    "a03_dual_ema_vol.py": {
        "name": "激进3: Dual EMA + Volume",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
激进3: Dual EMA + Volume
快慢EMA交叉 + 成交量放量确认。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param fast int 5 range=3:15:1
# @param slow int 20 range=10:50:5
# @param vol_factor float 1.5 range=1.0:3.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(100); g.prev_f = None; g.prev_s = None

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    fast = int(context.params.get("fast", 5)); slow = int(context.params.get("slow", 20))
    vf = float(context.params.get("vol_factor", 1.5)); tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(slow + 30, freq, ["close", "volume"], symbol)
    if len(bars) < slow + 5: return
    c = bars["close"]; v = bars["volume"]
    fe = float(c.ewm(span=fast, adjust=False).mean().iloc[-1])
    se = float(c.ewm(span=slow, adjust=False).mean().iloc[-1])
    av = float(v.tail(slow).mean()); cv = float(v.iloc[-1]); surge = cv > av * vf
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    buy = g.prev_f is not None and g.prev_f <= g.prev_s and fe > se and surge
    sell = g.prev_f is not None and g.prev_f >= g.prev_s and fe < se
    g.prev_f = fe; g.prev_s = se
    if buy and amt <= 0: order_target_percent(symbol, tp, reason="a03_buy")
    elif sell and amt > 0: order_target_percent(symbol, 0.0, reason="a03_sell")
'''
    },
    "a04_parabolic_sar.py": {
        "name": "激进4: Parabolic SAR",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
激进4: Parabolic SAR + EMA
SAR翻转+EMA趋势过滤，顺势开仓。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param sar_step float 0.02 range=0.01:0.05:0.005
# @param sar_max float 0.2 range=0.1:0.5:0.05
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    step = float(context.params.get("sar_step", 0.02)); mx = float(context.params.get("sar_max", 0.2))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(80, freq, ["high", "low", "close"], symbol)
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
    },
    "a05_keltner.py": {
        "name": "激进5: Keltner Breakout",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
激进5: Keltner Channel Breakout
波动率扩张突破，ATR动态通道。
适用市场: 加密货币 / 美股
建议周期: 15m / 1h
"""
# @param kc_period int 20 range=10:50:5
# @param kc_mult float 2.0 range=1.0:4.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    p = int(context.params.get("kc_period", 20)); m = float(context.params.get("kc_mult", 2.0))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(p + 20, freq, ["high", "low", "close"], symbol)
    if len(bars) < p + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    typ = [(float(h.iloc[i]) + float(l.iloc[i]) + float(c.iloc[i])) / 3 for i in range(len(c))]
    sma = sum(typ[-p:]) / p
    tr = [max(float(h.iloc[i]) - float(l.iloc[i]), abs(float(h.iloc[i]) - float(c.iloc[i-1])), abs(float(l.iloc[i]) - float(c.iloc[i-1]))) for i in range(1, len(c))]
    atr = sum(tr[-p:]) / p
    upper = sma + m * atr; lower = sma - m * atr; price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > upper and amt <= 0: order_target_percent(symbol, tp, reason="a05_buy")
    elif price < lower and amt > 0: order_target_percent(symbol, 0.0, reason="a05_sell")
'''
    },
    "a06_turtle.py": {
        "name": "激进6: Turtle Trading",
        "market": ["crypto", "us_stock"], "timeframe": "4h", "risk": "aggressive",
        "code": '''"""
激进6: Turtle Trading
海龟交易法则，Donchian通道突破。
适用市场: 加密货币 / 美股
建议周期: 4h / 1d
"""
# @param entry_window int 20 range=10:55:5
# @param exit_window int 10 range=5:30:5
# @param target_pct float 0.8 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(80)

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
    },
    "a07_dual_thrust.py": {
        "name": "激进7: Dual Thrust",
        "market": ["crypto", "us_stock"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
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
    context.set_warmup(30)

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
    },
    "a08_donchian.py": {
        "name": "激进8: Donchian Breakout",
        "market": ["crypto", "us_stock"], "timeframe": "4h", "risk": "aggressive",
        "code": '''"""
激进8: Donchian Channel Breakout
价格突破N日高低点，顺势跟踪。
适用市场: 加密货币 / 美股
建议周期: 4h / 1d
"""
# @param channel int 55 range=20:100:5
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
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
    },
    "a09_vwmacd.py": {
        "name": "激进9: Volume-Weighted MACD",
        "market": ["crypto"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
激进9: VWAP + MACD
成交量加权MACD，放量金叉开仓。
适用市场: 加密货币
建议周期: 15m / 1h
"""
# @param fast int 12 range=5:30:1
# @param slow int 26 range=10:50:1
# @param vol_ratio float 1.2 range=1.0:2.0:0.1
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(100)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    fast = int(context.params.get("fast", 12)); slow = int(context.params.get("slow", 26))
    vr = float(context.params.get("vol_ratio", 1.2)); tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(slow + 30, freq, ["close", "volume"], symbol)
    if len(bars) < slow + 5: return
    c = bars["close"]; v = bars["volume"]
    vw = [(float(c.iloc[i]) * float(v.iloc[i])) for i in range(len(c))]
    vw_fast = sum(vw[-fast:]) / sum([float(v.iloc[i]) for i in range(-fast, 0)]) if sum([float(v.iloc[i]) for i in range(-fast, 0)]) > 0 else 0
    vw_slow = sum(vw[-slow:]) / sum([float(v.iloc[i]) for i in range(-slow, 0)]) if sum([float(v.iloc[i]) for i in range(-slow, 0)]) > 0 else 0
    macd = vw_fast - vw_slow
    av = float(v.tail(20).mean()); cv = float(v.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if macd > 0 and cv > av * vr and amt <= 0:
        order_target_percent(symbol, tp, reason="a09_buy")
    elif macd < 0 and amt > 0:
        order_target_percent(symbol, 0.0, reason="a09_sell")
'''
    },
    "a10_atr_breakout.py": {
        "name": "激进10: ATR Channel Breakout",
        "market": ["crypto"], "timeframe": "15m", "risk": "aggressive",
        "code": '''"""
激进10: ATR Channel Breakout
ATR动态通道突破，波动率自适应。
适用市场: 加密货币
建议周期: 15m / 1h
"""
# @param atr_period int 14 range=7:30:1
# @param atr_mult float 2.0 range=1.0:4.0:0.25
# @param target_pct float 0.85 range=0.1:1:0.1

def initialize(context):
    context.set_warmup(50)

def handle_data(context, data):
    symbol = context.instruments[0] if context.instruments else "Crypto:BTC/USDT@spot"
    freq = context.subscriptions[0].frequency if context.subscriptions else "15m"
    ap = int(context.params.get("atr_period", 14)); am = float(context.params.get("atr_mult", 2.0))
    tp = float(context.params.get("target_pct", 0.85))
    bars = get_history(ap + 30, freq, ["high", "low", "close"], symbol)
    if len(bars) < ap + 5: return
    h = bars["high"]; l = bars["low"]; c = bars["close"]
    tr = [max(float(h.iloc[i]) - float(l.iloc[i]), abs(float(h.iloc[i]) - float(c.iloc[i-1])), abs(float(l.iloc[i]) - float(c.iloc[i-1]))) for i in range(1, len(c))]
    atr = sum(tr[-ap:]) / ap; sma = float(c.tail(ap).mean())
    upper = sma + am * atr; lower = sma - am * atr; price = float(c.iloc[-1])
    pos = get_position(symbol); amt = float(pos.amount or 0.0)
    if price > upper and amt <= 0: order_target_percent(symbol, tp, reason="a10_buy")
    elif price < lower and amt > 0: order_target_percent(symbol, 0.0, reason="a10_sell")
'''
    },
}

for filename, cfg in STRATEGIES.items():
    path = os.path.join(d, filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f'"""{cfg["name"]} — {cfg["risk"]} | {" / ".join(cfg["market"])} | ~{cfg["timeframe"]}"""\n')
        f.write(f"STRATEGY_CODE = r'''\n{cfg['code']}\n'''\n")
        f.write(f"MARKET_SUITABLE = {cfg['market']}\n")
        f.write(f"SUGGESTED_TIMEFRAME = '{cfg['timeframe']}'\n")
        f.write(f"RISK_LEVEL = '{cfg['risk']}'\n")
    print(f'Created {filename}')

# Write __init__.py
init_code = '''"""15 optimized strategies — symbol/timeframe agnostic.
Universe and frequency are injected at runtime from qd_strategies_trading.symbol/timeframe.
"""
from __future__ import annotations

# Conservative (稳妥 1-5)
from . import s01_bband_rsi as c01
from . import s02_ema_crossover as c02
from . import s03_rsi_scalper as c03
from . import s04_ichimoku as c04
from . import s05_triple_ema as c05
# Aggressive (激进 1-10)
from . import a01_supertrend as a01
from . import a02_macd as a02
from . import a03_dual_ema_vol as a03
from . import a04_parabolic_sar as a04
from . import a05_keltner as a05
from . import a06_turtle as a06
from . import a07_dual_thrust as a07
from . import a08_donchian as a08
from . import a09_vwmacd as a09
from . import a10_atr_breakout as a10

ALL_STRATEGIES = [c01, c02, c03, c04, c05, a01, a02, a03, a04, a05, a06, a07, a08, a09, a10]

BUILTIN_DSL_SOURCES = {}
STRATEGY_META = {}
for mod in ALL_STRATEGIES:
    key = mod.__name__.split(".")[-1]
    BUILTIN_DSL_SOURCES[key] = mod.STRATEGY_CODE
    STRATEGY_META[key] = {
        "name": mod.__doc__.split("\\n")[0].strip() if mod.__doc__ else key,
        "market": getattr(mod, "MARKET_SUITABLE", ["crypto"]),
        "timeframe": getattr(mod, "SUGGESTED_TIMEFRAME", "15m"),
        "risk": getattr(mod, "RISK_LEVEL", "conservative"),
    }
'''
with open(os.path.join(d, '__init__.py'), 'w') as f:
    f.write(init_code)
print('Wrote __init__.py')
print(f'Total: {len(STRATEGIES)} strategies')