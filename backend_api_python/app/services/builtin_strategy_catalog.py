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
            "smc-structure",
            "smc-v1",
            StrategyFamily.SMC,
            "smc-structure-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("lookback", "3"),),
        ),
        StrategyDefinition(
            "ict-liquidity-displacement",
            "ict-v1",
            StrategyFamily.ICT,
            "ict-liquidity-parameters-v1",
            "gate-ohlcv-pit-v1",
            (StrategyParameterFact("lookback", "3"), StrategyParameterFact("multiplier", "1.5")),
        ),
    )


__all__ = ["BUILTIN_STRATEGY_CATALOG_VERSION", "builtin_strategy_catalog"]
