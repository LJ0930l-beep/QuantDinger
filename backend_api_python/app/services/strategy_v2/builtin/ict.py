"""Inner Circle Trader (ICT) — builtin strategy implementation.

Detects Fair Value Gaps (FVG), Optimal Trade Entry (OTE) zones,
and killzone time windows for Bitcoin perpetual on 5m/15m/1h.
"""

STRATEGY_CODE = r'''
"""
Inner Circle Trader — FVG / OTE / Killzone
BTC perpetual multi-timeframe ICT concepts.
"""

# @param lookback int 3 range=2:10:1
# @param multiplier float 1.5 range=1.0:3.0:0.25
# @param risk_pct float 0.015 range=0.005:0.05:0.005

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark("Crypto:BTC/USDT@spot")
    context.subscribe(frequency="5m")
    context.set_warmup(300)
    context.allow_leverage(max_leverage=10, min_leverage=1)

    g.fvg_bullish = None
    g.fvg_bearish = None
    g.killzone_active = False


def handle_data(context, data):
    lookback = int(context.params.get("lookback", 3))
    multiplier = float(context.params.get("multiplier", 1.5))
    risk_pct = float(context.params.get("risk_pct", 0.015))

    # ---- Killzone detection (London Open 08:00 UTC, NY Open 13:30 UTC) ----
    current_hour = int(context.current_dt.hour if hasattr(context, 'current_dt') else 12)
    g.killzone_active = (7 <= current_hour <= 10) or (13 <= current_hour <= 16)

    bars_1h = get_history(lookback * 6 + 20, "1h", ["high", "low", "close"], g.symbol)
    bars_15m = get_history(lookback * 4 + 10, "15m", ["high", "low", "close"], g.symbol)
    bars_5m = get_history(lookback * 12 + 10, "5m", ["high", "low", "close", "open"], g.symbol)
    if len(bars_1h) < 20 or len(bars_15m) < 10 or len(bars_5m) < 10:
        return

    # ---- 1h trend context ----
    hh = bars_1h["high"]
    hl = bars_1h["low"]
    hc = bars_1h["close"]
    recent_high_1h = float(hh.iloc[-20:].max())
    recent_low_1h = float(hl.iloc[-20:].min())
    trend_bullish = float(hc.iloc[-1]) > (recent_high_1h + recent_low_1h) / 2

    # ---- Fair Value Gap (FVG) on 15m ----
    fh = bars_15m["high"]
    fl = bars_15m["low"]
    fc = bars_15m["close"]

    fvg_bullish = None  # (top, bottom) — price should retrace into this zone
    fvg_bearish = None

    # Bullish FVG: 3-candle pattern, gap between candle[0].high and candle[2].low
    for i in range(-lookback * 4, -3):
        if fc.iloc[i+1] > fc.iloc[i] and fh.iloc[i] < fl.iloc[i+2]:
            gap_high = float(fl.iloc[i+2])
            gap_low = float(fh.iloc[i])
            if gap_high > gap_low:
                fvg_bullish = (gap_high, gap_low)
                break

    # Bearish FVG: gap between candle[0].low and candle[2].high
    for i in range(-lookback * 4, -3):
        if fc.iloc[i+1] < fc.iloc[i] and fl.iloc[i] > fh.iloc[i+2]:
            gap_low = float(fh.iloc[i+2])
            gap_high = float(fl.iloc[i])
            if gap_high > gap_low:
                fvg_bearish = (gap_high, gap_low)
                break

    # ---- Optimal Trade Entry (OTE) — 61.8% - 79% of recent swing ----
    recent_swing_high = float(fh.iloc[-lookback*4:].max())
    recent_swing_low = float(fl.iloc[-lookback*4:].min())
    swing_range = recent_swing_high - recent_swing_low
    ote_low = recent_swing_high - swing_range * 0.79
    ote_high = recent_swing_high - swing_range * 0.618

    # ---- Entry Logic ----
    bh = bars_5m["high"]
    bl = bars_5m["low"]
    bc = bars_5m["close"]
    current = float(bc.iloc[-1])
    position = get_position(g.symbol)
    amount = float(position.amount or 0.0)

    if g.killzone_active:
        if trend_bullish and fvg_bullish and amount <= 0:
            fvg_top, fvg_bot = fvg_bullish
            if fvg_bot * 0.998 <= current <= fvg_top:
                order_target_percent(g.symbol, 0.6, reason="ict_bullish_fvg_entry")
        elif not trend_bullish and fvg_bearish and amount >= 0:
            fvg_top, fvg_bot = fvg_bearish
            if fvg_bot <= current <= fvg_top * 1.002:
                order_target_percent(g.symbol, -0.6, reason="ict_bearish_fvg_entry")

    # ---- OTE entry with multiplier filter ----
    if trend_bullish and swing_range > 0 and ote_low <= current <= ote_high and amount <= 0:
        if swing_range * multiplier > current * 0.005:
            order_target_percent(g.symbol, 0.4, reason="ict_bullish_ote_entry")
    elif not trend_bullish and swing_range > 0 and ote_low <= current <= ote_high and amount >= 0:
        if swing_range * multiplier > current * 0.005:
            order_target_percent(g.symbol, -0.4, reason="ict_bearish_ote_entry")

    # ---- Risk management: exit outside killzone ----
    if not g.killzone_active and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="ict_killzone_exit")
'''
