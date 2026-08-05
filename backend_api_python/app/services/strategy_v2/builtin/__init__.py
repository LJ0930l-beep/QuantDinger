"""Builtin strategy implementations for Strategy API V2.

Each module contains a ``STRATEGY_CODE`` constant with the full DSL source,
ready for compilation by ``strategy_v2.contract.compile_strategy_v2()``.
"""

from __future__ import annotations

from . import smc
from . import ict
from . import trend_following
from . import mean_reversion

__all__ = ["smc", "ict", "trend_following", "mean_reversion"]

# Registry mapping catalog identity -> DSL source
BUILTIN_DSL_SOURCES = {
    "smc-structure": smc.STRATEGY_CODE,
    "ict-liquidity-displacement": ict.STRATEGY_CODE,
    "ema-adx-trend": trend_following.STRATEGY_CODE,
    "bollinger-rsi": mean_reversion.STRATEGY_CODE,
}
