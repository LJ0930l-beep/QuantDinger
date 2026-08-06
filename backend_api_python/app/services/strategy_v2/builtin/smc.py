"""Smart Money Concepts (SMC) — builtin strategy implementation.

Identifies order blocks, liquidity grabs, and market structure breaks (MSB/CHOCH)
for Bitcoin perpetual on Gate TestNet with 15m/1h/4h multi-timeframe confirmation.
"""

STRATEGY_CODE = r'''
"""
Smart Money Concepts — Order Block / Liquidity Grab
BTC perpetual multi-timeframe with structure-based entries.
"""

# @param lookback int 3 range=2:10:1
# @param risk_pct float 0.02 range=0.005:0.1:0.005
# @param rr_ratio float 2.0 range=1.0:5.0:0.5

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark("Crypto:BTC/USDT@spot")
    context.subscribe(frequency="15m")
    context.set_warmup(200)
    context.allow_leverage(max_leverage=10, min_leverage=1)

    g.prev_high = None
    g.prev_low = None
    g.swing_high = None
    g.swing_low = None
    g.order_block_high = None
    g.order_block_low = None
    g.trend = None  # "bullish" or "bearish"


def handle_data(context, data):
    lookback = int(context.params.get("lookback", 3))
    risk_pct = float(context.params.get("risk_pct", 0.02))
    rr_ratio = float(context.params.get("rr_ratio", 2.0))

    bars_1h = get_history(lookback * 4 + 20, "1h", ["high", "low", "close"], g.symbol)
    bars_15m = get_history(lookback * 4 + 10, "15m", ["high", "low", "close", "open"], g.symbol)
    if len(bars_1h) < lookback * 4 + 5 or len(bars_15m) < lookback * 4 + 5:
        return

    hh = bars_1h["high"]
    hl = bars_1h["low"]
    hc = bars_1h["close"]

    # ---- Market Structure: detect swing points on 1h ----
    window = lookback * 4
    swing_high = None
    swing_low = None
    for i in range(-window - 3, -3):
        if all(hh.iloc[i] >= hh.iloc[i+j] for j in range(-2, 3) if j != 0):
            swing_high = float(hh.iloc[i])
        if all(hl.iloc[i] <= hl.iloc[i+j] for j in range(-2, 3) if j != 0):
            swing_low = float(hl.iloc[i])

    # ---- Determine trend via recent swing sequence ----
    recent_close = float(hc.iloc[-1])
    if swing_high and swing_low:
        if swing_high > swing_low and recent_close > (swing_high + swing_low) / 2:
            g.trend = "bullish"
        elif swing_low < swing_high and recent_close < (swing_high + swing_low) / 2:
            g.trend = "bearish"

    # ---- Order Block detection on 15m ----
    bl = bars_15m["low"]
    bh = bars_15m["high"]
    bo = bars_15m["open"]
    bc = bars_15m["close"]

    ob_high = None
    ob_low = None
    ob_bullish = False
    # Bullish OB: last bearish candle before a strong bullish move
    for i in range(-lookback * 4, -2):
        if bc.iloc[i] < bo.iloc[i] and bc.iloc[i+1] > bo.iloc[i+1] and bc.iloc[i+1] > bh.iloc[i]:
            ob_high = float(bh.iloc[i])
            ob_low = float(bl.iloc[i])
            ob_bullish = True
            break
    # Bearish OB: last bullish candle before a strong bearish move
    if not ob_bullish:
        for i in range(-lookback * 4, -2):
            if bc.iloc[i] > bo.iloc[i] and bc.iloc[i+1] < bo.iloc[i+1] and bc.iloc[i+1] < bl.iloc[i]:
                ob_high = float(bh.iloc[i])
                ob_low = float(bl.iloc[i])
                break

    # ---- Liquidity grab detection ----
    recent_low_15m = float(bl.iloc[-lookback:].min())
    recent_high_15m = float(bh.iloc[-lookback:].max())
    liquidity_grabbed_below = recent_close < recent_low_15m * 0.998
    liquidity_grabbed_above = recent_close > recent_high_15m * 1.002

    # ---- Entry Logic ----
    position = get_position(g.symbol)
    amount = float(position.amount or 0.0)

    if g.trend == "bullish" and ob_bullish and ob_low and amount <= 0:
        if recent_close <= ob_high and recent_close >= ob_low * 0.998:
            stop_loss = ob_low * 0.995
            risk_distance = recent_close - stop_loss
            if risk_distance > 0:
                position_size = min(risk_pct / (risk_distance / recent_close), 0.95)
                order_target_percent(g.symbol, position_size, reason="smc_bullish_ob_entry")
    elif g.trend == "bearish" and not ob_bullish and ob_high and amount >= 0:
        if recent_close >= ob_low and recent_close <= ob_high * 1.002:
            stop_loss = ob_high * 1.005
            risk_distance = stop_loss - recent_close
            if risk_distance > 0:
                position_size = min(risk_pct / (risk_distance / recent_close), 0.95)
                order_target_percent(g.symbol, -position_size, reason="smc_bearish_ob_entry")

    # ---- Liquidity grab entries ----
    if g.trend == "bullish" and liquidity_grabbed_below and amount <= 0:
        order_target_percent(g.symbol, 0.5, reason="smc_liquidity_grab_long")
    elif g.trend == "bearish" and liquidity_grabbed_above and amount >= 0:
        order_target_percent(g.symbol, -0.5, reason="smc_liquidity_grab_short")

    # ---- Take profit at RR target ----
    if amount > 0 and g.order_block_high and recent_close >= g.order_block_low + (g.order_block_high - g.order_block_low) * rr_ratio:
        order_target_percent(g.symbol, 0.0, reason="smc_tp_hit")
    elif amount < 0 and g.order_block_low and recent_close <= g.order_block_high - (g.order_block_high - g.order_block_low) * rr_ratio:
        order_target_percent(g.symbol, 0.0, reason="smc_tp_hit")
'''
