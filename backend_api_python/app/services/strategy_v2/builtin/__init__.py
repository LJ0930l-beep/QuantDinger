"""Builtin strategy implementations — 10 proven trading strategies.

Sources: freqtrade-strategies, Gekko, Bitcoin-Algo-Trading, abu quant system.
"""

from __future__ import annotations

from . import bband_rsi
from . import ema_crossover
from . import macd_strategy
from . import supertrend
from . import ichimoku
from . import dual_ema_volume
from . import parabolic_sar
from . import keltner_breakout
from . import rsi_scalper
from . import turtle_trading
from . import triple_ema
from . import dual_thrust

__all__ = [
    "bband_rsi", "ema_crossover", "macd_strategy", "supertrend",
    "ichimoku", "dual_ema_volume", "parabolic_sar", "keltner_breakout",
    "rsi_scalper", "turtle_trading", "triple_ema", "dual_thrust",
]

BUILTIN_DSL_SOURCES = {
    "bband-rsi": bband_rsi.STRATEGY_CODE,
    "ema-crossover": ema_crossover.STRATEGY_CODE,
    "macd-strategy": macd_strategy.STRATEGY_CODE,
    "supertrend-adx": supertrend.STRATEGY_CODE,
    "ichimoku-cloud": ichimoku.STRATEGY_CODE,
    "dual-ema-volume": dual_ema_volume.STRATEGY_CODE,
    "parabolic-sar": parabolic_sar.STRATEGY_CODE,
    "keltner-breakout": keltner_breakout.STRATEGY_CODE,
    "rsi-scalper": rsi_scalper.STRATEGY_CODE,
    "turtle-trading": turtle_trading.STRATEGY_CODE,
    "triple-ema": triple_ema.STRATEGY_CODE,
    "dual-thrust": dual_thrust.STRATEGY_CODE,
}
