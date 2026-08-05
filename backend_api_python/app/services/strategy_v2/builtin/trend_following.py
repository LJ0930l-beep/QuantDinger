"""Trend Following — builtin strategy implementation.

Multi-timeframe SMA crossover with ADX trend strength confirmation
for Bitcoin perpetual on 15m/1h/4h.
"""

STRATEGY_CODE = r'''
"""
Trend Following — SMA + ADX Crossover
BTC perpetual multi-timeframe trend strategy.
"""

# @param fast_period int 12 range=5:50:1
# @param slow_period int 26 range=10:100:1
# @param adx_period int 14 range=7:30:1
# @param adx_threshold int 25 range=15:50:5
# @param target_pct float 0.8 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark("Crypto:BTC/USDT@spot")
    context.subscribe(frequency="15m")
    context.set_warmup(200)
    context.allow_leverage(max_leverage=10, min_leverage=1)

    g.prev_fast = None
    g.prev_slow = None


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 12))
    slow_period = int(context.params.get("slow_period", 26))
    adx_period = int(context.params.get("adx_period", 14))
    adx_threshold = int(context.params.get("adx_threshold", 25))
    target_pct = float(context.params.get("target_pct", 0.8))

    if fast_period >= slow_period:
        return

    max_period = max(fast_period, slow_period, adx_period)
    bars_15m = get_history(max_period + 30, "15m", ["high", "low", "close"], g.symbol)
    bars_1h = get_history(60, "1h", ["close"], g.symbol)
    if len(bars_15m) < max_period + 5:
        return

    close = bars_15m["close"]
    high = bars_15m["high"]
    low = bars_15m["low"]

    # ---- Fast/Slow SMA on 15m ----
    fast = float(close.tail(fast_period).mean())
    slow = float(close.tail(slow_period).mean())

    # ---- ADX calculation ----
    adx_val = _compute_adx(high, low, close, adx_period)

    # ---- 1h trend filter (SMA 50) ----
    trend_1h_bullish = True
    if len(bars_1h) >= 50:
        close_1h = bars_1h["close"]
        ma50_1h = float(close_1h.tail(50).mean())
        trend_1h_bullish = float(close_1h.iloc[-1]) > ma50_1h

    # ---- Detect crossover ----
    if g.prev_fast is None:
        g.prev_fast = fast
        g.prev_slow = slow
        return

    golden_cross = g.prev_fast <= g.prev_slow and fast > slow
    death_cross = g.prev_fast >= g.prev_slow and fast < slow
    g.prev_fast = fast
    g.prev_slow = slow

    position = get_position(g.symbol)
    amount = float(position.amount or 0.0)

    # ---- Entry: Golden Cross + ADX > threshold + 1h bullish ----
    if golden_cross and adx_val > adx_threshold and trend_1h_bullish and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="trend_golden_cross_long")

    # ---- Entry: Death Cross + ADX > threshold + 1h bearish ----
    if death_cross and adx_val > adx_threshold and not trend_1h_bullish and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="trend_death_cross_short")

    # ---- Exit: cross back ----
    if amount > 0 and fast <= slow:
        order_target_percent(g.symbol, 0.0, reason="trend_exit_long")
    elif amount < 0 and fast >= slow:
        order_target_percent(g.symbol, 0.0, reason="trend_exit_short")

    # ---- ADX weakening exit ----
    if adx_val < 15 and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="trend_adx_weakening")


def _compute_adx(high, low, close, period):
    """Compute ADX using Wilder's smoothing."""
    n = len(high)
    if n < period + 1:
        return 0.0
    tr_list = []
    plus_dm = []
    minus_dm = []
    for i in range(1, n):
        h = float(high.iloc[i])
        l = float(low.iloc[i])
        pc = float(close.iloc[i-1])
        ph = float(high.iloc[i-1])
        pl = float(low.iloc[i-1])
        tr_val = max(h - l, abs(h - pc), abs(l - pc))
        up = h - ph
        dn = pl - l
        tr_list.append(tr_val)
        plus_dm.append(up if up > dn and up > 0 else 0.0)
        minus_dm.append(dn if dn > up and dn > 0 else 0.0)
    if len(tr_list) < period:
        return 0.0
    atr = sum(tr_list[:period]) / period
    sum_tr = sum(tr_list[:period])
    sum_p = sum(plus_dm[:period])
    sum_m = sum(minus_dm[:period])
    for i in range(period, len(tr_list)):
        sum_tr = sum_tr - sum_tr / period + tr_list[i]
        sum_p = sum_p - sum_p / period + plus_dm[i]
        sum_m = sum_m - sum_m / period + minus_dm[i]
    atr_smooth = sum_tr / period if sum_tr > 0 else 0.0001
    pdi = 100 * (sum_p / period) / atr_smooth
    mdi = 100 * (sum_m / period) / atr_smooth
    dx_sum = pdi + mdi
    adx = 100 * abs(pdi - mdi) / dx_sum if dx_sum > 0 else 0.0
    return adx
'''
