"""Deterministic strategy factory for research and simulation modes.

The factory turns a typed strategy definition plus point-in-time bars into a
typed signal.  It has no account, order, risk, persistence, exchange, worker,
or LIVE authority.  Unsupported families fail closed instead of silently
falling back to a different strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.strategy_library_contracts import (
    StrategyDefinition,
    StrategyFamily,
    StrategySignalFact,
)
from app.domain.strategy_signal_contracts import (
    StrategySignalContractError,
    build_strategy_signal,
)


class StrategyFactoryError(ValueError):
    """The requested strategy cannot be deterministically evaluated."""


@dataclass(frozen=True, slots=True)
class StrategyFactory:
    """Versioned strategy family registry with fail-closed dispatch."""

    supported_families: tuple[StrategyFamily, ...] = (
        StrategyFamily.EMA_ADX_TREND,
        StrategyFamily.DONCHIAN_ATR,
        StrategyFamily.BOLLINGER_RSI,
        StrategyFamily.BUY_AND_HOLD,
        StrategyFamily.SMC,
        StrategyFamily.ICT,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.supported_families, tuple) or not self.supported_families:
            raise StrategyFactoryError("supported_families must be a non-empty tuple")
        if any(not isinstance(item, StrategyFamily) for item in self.supported_families):
            raise StrategyFactoryError("supported_families must use typed families")
        if len(set(self.supported_families)) != len(self.supported_families):
            raise StrategyFactoryError("supported_families must be unique")

    def generate_signal(
        self,
        definition: StrategyDefinition,
        bars: Iterable[BacktestBar],
        *,
        signal_id: str,
        data_snapshot_id: str,
    ) -> StrategySignalFact:
        if not isinstance(definition, StrategyDefinition):
            raise StrategyFactoryError("definition must be typed")
        if definition.family not in self.supported_families:
            raise StrategyFactoryError(f"strategy family is not enabled: {definition.family.value}")
        try:
            return build_strategy_signal(
                definition,
                tuple(bars),
                signal_id=signal_id,
                data_snapshot_id=data_snapshot_id,
            )
        except StrategySignalContractError as exc:
            raise StrategyFactoryError("strategy signal could not be built") from exc


__all__ = ["StrategyFactory", "StrategyFactoryError"]
