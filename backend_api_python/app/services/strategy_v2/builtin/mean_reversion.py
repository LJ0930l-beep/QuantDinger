"""Mean Reversion — builtin strategy implementation.

Bollinger Bands + RSI oversold/overbought with volume confirmation
for Bitcoin perpetual on 5m/15m/1h.
"""

STRATEGY_CODE = r'''
"""
Mean Reversion — Bollinger Bands + RSI
BTC perpetual 15m mean-reversion with volume confirmation.
"""

# @param window int 20 range=10:50:5
# @param deviation float 2.0 range=1.0:3.0:0.25
# @param rsi_period int 14 range=7:30:1
# @param rsi_oversold int 30 range=15:40:5
# @param rsi_overbought int 70 range=60:85:5
# @param target_pct float 0.75 range=0.1:1:0.1

def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark("Crypto:BTC/USDT@spot")
    context.subscribe(frequency="15m")
    context.set_warmup(200)
    context.allow_leverage(max_leverage=5, min_leverage=1)


def handle_data(context, data):
    window = int(context.params.get("window", 20))
    deviation = float(context.params.get("deviation", 2.0))
    rsi_period = int(context.params.get("rsi_period", 14))
    rsi_oversold = int(context.params.get("rsi_oversold", 30))
    rsi_overbought = int(context.params.get("rsi_overbought", 70))
    target_pct = float(context.params.get("target_pct", 0.75))

    max_period = max(window, rsi_period)
    bars_15m = get_history(max_period + 30, "15m", ["high", "low", "close", "volume"], g.symbol)
    bars_1h = get_history(30, "1h", ["close"], g.symbol)
    if len(bars_15m) < max_period + 5:
        return

    close = bars_15m["close"]
    volume = bars_15m["volume"]

    # ---- Bollinger Bands ----
    sma = float(close.tail(window).mean())
    std = float(close.tail(window).std())
    upper = sma + deviation * std
    lower = sma - deviation * std
    current = float(close.iloc[-1])
    bb_position = (current - lower) / (upper - lower) if (upper - lower) > 0 else 0.5

    # ---- RSI ----
    rsi = _compute_rsi(close, rsi_period)

    # ---- Volume confirmation ----
    avg_vol = float(volume.tail(window).mean())
    current_vol = float(volume.iloc[-1])
    vol_surge = current_vol > avg_vol * 1.2

    # ---- 1h trend filter: avoid counter-trend during strong moves ----
    trend_neutral = True
    if len(bars_1h) >= 20:
        close_1h = bars_1h["close"]
        ma20_1h = float(close_1h.tail(20).mean())
        pct_from_ma = abs(float(close_1h.iloc[-1]) - ma20_1h) / ma20_1h
        trend_neutral = pct_from_ma < 0.03  # within 3% of 1h MA20

    position = get_position(g.symbol)
    amount = float(position.amount or 0.0)

    # ---- Entry: Oversold + below lower band + volume surge ----
    if rsi < rsi_oversold and bb_position < 0.05 and vol_surge and trend_neutral and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="mean_reversion_oversold_long")

    # ---- Entry: Overbought + above upper band + volume surge ----
    if rsi > rsi_overbought and bb_position > 0.95 and vol_surge and trend_neutral and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="mean_reversion_overbought_short")

    # ---- Exit: Return to mean ----
    if amount > 0 and bb_position > 0.45:
        order_target_percent(g.symbol, 0.0, reason="mean_reversion_exit_long")
    elif amount < 0 and bb_position < 0.55:
        order_target_percent(g.symbol, 0.0, reason="mean_reversion_exit_short")

    # ---- Stop loss: exit if price breaks bands severely ----
    if amount > 0 and current < lower * 0.98:
        order_target_percent(g.symbol, 0.0, reason="mean_reversion_stop_long")
    elif amount < 0 and current > upper * 1.02:
        order_target_percent(g.symbol, 0.0, reason="mean_reversion_stop_short")


def _compute_rsi(close, period):
    """Compute RSI using Wilder's smoothing."""
    n = len(close)
    if n < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, n):
        delta = float(close.iloc[i]) - float(close.iloc[i-1])
        gains.append(delta if delta > 0 else 0.0)
        losses.append(-delta if delta < 0 else 0.0)
    if len(gains) < period:
        return 50.0
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
'''
