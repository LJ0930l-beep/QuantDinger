"""15 optimized strategies — no symbol/timeframe restrictions, 稳妥/激进 naming."""
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
for mod in ALL_STRATEGIES:
    key = mod.__name__.split(".")[-1]
    BUILTIN_DSL_SOURCES[key] = mod.STRATEGY_CODE

STRATEGY_META = {}
for mod in ALL_STRATEGIES:
    key = mod.__name__.split(".")[-1]
    STRATEGY_META[key] = {
        "name": mod.__doc__.split("\n")[0].strip() if mod.__doc__ else key,
        "market": getattr(mod, "MARKET_SUITABLE", ["crypto"]),
        "timeframe": getattr(mod, "SUGGESTED_TIMEFRAME", "15m"),
        "risk": getattr(mod, "RISK_LEVEL", "conservative"),
    }
