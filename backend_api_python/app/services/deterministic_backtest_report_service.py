"""Assemble a replayable backtest report from explicit execution facts.

This service closes the gap between the deterministic strategy/execution trace
and the existing report API contract.  Equity and trade facts remain caller
owned: the service never infers PnL, fees, funding, or mark prices from an
execution decision and never persists or publishes the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.backtest_metrics_contracts import (
    BacktestEquityPoint,
    BacktestMetricsError,
    BacktestTradeResult,
    WalkForwardWindow,
    calculate_backtest_metrics,
)
from app.domain.backtest_report_contracts import BacktestReportSnapshot, BacktestReportError, build_backtest_report
from app.services.deterministic_backtest_service import DeterministicStrategyBacktest


class DeterministicBacktestReportError(ValueError):
    """Explicit execution facts cannot form a safe report."""


@dataclass(frozen=True, slots=True)
class DeterministicBacktestReportService:
    """Build a typed report without adding any hidden valuation assumptions."""

    def build(
        self,
        result: DeterministicStrategyBacktest,
        equity_points: tuple[BacktestEquityPoint, ...],
        trades: tuple[BacktestTradeResult, ...] = (),
        *,
        walk_forward_windows: tuple[WalkForwardWindow, ...] = (),
        report_created_at: datetime,
    ) -> BacktestReportSnapshot:
        if not isinstance(result, DeterministicStrategyBacktest):
            raise DeterministicBacktestReportError("result must be a typed deterministic backtest")
        if not isinstance(equity_points, tuple) or any(not isinstance(item, BacktestEquityPoint) for item in equity_points):
            raise DeterministicBacktestReportError("equity_points must be a typed tuple")
        if not isinstance(trades, tuple) or any(not isinstance(item, BacktestTradeResult) for item in trades):
            raise DeterministicBacktestReportError("trades must be a typed tuple")
        if not isinstance(walk_forward_windows, tuple) or any(not isinstance(item, WalkForwardWindow) for item in walk_forward_windows):
            raise DeterministicBacktestReportError("walk_forward_windows must be a typed tuple")
        try:
            metrics = calculate_backtest_metrics(equity_points, trades)
            return build_backtest_report(
                result.run,
                result.dataset,
                metrics,
                walk_forward_windows,
                report_created_at=report_created_at,
            )
        except (BacktestMetricsError, BacktestReportError) as exc:
            raise DeterministicBacktestReportError("explicit backtest facts are invalid") from exc
        except Exception as exc:
            raise DeterministicBacktestReportError("backtest report assembly failed closed") from exc


__all__ = ["DeterministicBacktestReportError", "DeterministicBacktestReportService"]
