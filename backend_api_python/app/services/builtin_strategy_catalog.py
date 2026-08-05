"""Deterministic built-in Strategy Factory catalog for read-only consumers.

The catalog is deliberately a pure provider.  It exposes the strategy
definitions that the non-live research pipeline already understands, but it
does not evaluate signals, allocate risk, persist anything, or create an
execution authority.  Applications may replace the provider explicitly when
they have a reviewed catalog source.
"""

from __future__ import annotations

from app.domain.strategy_library_contracts import (
    StrategyDefinition,
    StrategyFamily,
    StrategyParameterFact,
)


BUILTIN_STRATEGY_CATALOG_VERSION = "builtin-strategy-catalog-v1"


def builtin_strategy_catalog() -> tuple[StrategyDefinition, ...]:
    """Return immutable, versioned definitions supported by ``StrategyFactory``.

    The values are metadata only.  No account, credential, market connection,
    order, or live-trading fact is represented here.
    """

    return (
        StrategyDefinition(
            "ema-adx-trend",
            "ema-adx-v1",
            StrategyFamily.EMA_ADX_TREND,
            "ema-adx-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("fast_period", "12"), StrategyParameterFact("slow_period", "26"), StrategyParameterFact("adx_period", "14")),
            ("15m", "1h", "4h"),
            ("crypto", "us_stock"),
        ),
        StrategyDefinition(
            "donchian-atr",
            "donchian-atr-v1",
            StrategyFamily.DONCHIAN_ATR,
            "donchian-atr-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("lookback", "20"), StrategyParameterFact("atr_period", "14")),
            ("1h", "4h", "1d"),
            ("crypto", "us_stock"),
        ),
        StrategyDefinition(
            "bollinger-rsi",
            "bollinger-rsi-v1",
            StrategyFamily.BOLLINGER_RSI,
            "bollinger-rsi-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("window", "20"), StrategyParameterFact("deviation", "2"), StrategyParameterFact("rsi_period", "14")),
            ("5m", "15m", "1h"),
            ("crypto", "us_stock"),
        ),
        StrategyDefinition(
            "dual-thrust",
            "dual-thrust-v1",
            StrategyFamily.DUAL_THRUST,
            "dual-thrust-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("lookback", "4"), StrategyParameterFact("upper_factor", "0.5"), StrategyParameterFact("lower_factor", "0.5")),
            ("15m", "1h", "4h"),
            ("crypto",),
        ),
        StrategyDefinition(
            "buy-and-hold",
            "buy-and-hold-v1",
            StrategyFamily.BUY_AND_HOLD,
            "buy-and-hold-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("rebalance", "none"),),
            ("1d",),
            ("crypto", "us_stock"),
        ),
        StrategyDefinition(
            "smc-structure",
            "smc-v1",
            StrategyFamily.SMC,
            "smc-structure-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("lookback", "3"),),
            ("15m", "1h", "4h"),
            ("crypto",),
        ),
        StrategyDefinition(
            "ict-liquidity-displacement",
            "ict-v1",
            StrategyFamily.ICT,
            "ict-liquidity-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("lookback", "3"), StrategyParameterFact("multiplier", "1.5")),
            ("5m", "15m", "1h"),
            ("crypto",),
        ),
        StrategyDefinition(
            "rsi-mean-reversion-5m",
            "rsi-mean-reversion-v1",
            StrategyFamily.BOLLINGER_RSI,
            "rsi-mean-reversion-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("window", "14"), StrategyParameterFact("deviation", "2"), StrategyParameterFact("rsi_period", "7")),
            ("5m",),
            ("crypto",),
        ),
        StrategyDefinition(
            "ema-adx-momentum-15m",
            "ema-adx-momentum-v1",
            StrategyFamily.EMA_ADX_TREND,
            "ema-adx-momentum-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("fast_period", "8"), StrategyParameterFact("slow_period", "21"), StrategyParameterFact("adx_period", "14")),
            ("15m",),
            ("crypto",),
        ),
    )


__all__ = ["BUILTIN_STRATEGY_CATALOG_VERSION", "builtin_strategy_catalog"]
