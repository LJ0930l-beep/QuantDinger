"""Read-only deterministic backtest orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot
from app.domain.backtest_metrics_contracts import BacktestMetrics, WalkForwardWindow
from app.domain.backtest_report_contracts import BacktestReportSnapshot, build_backtest_report
from app.domain.deterministic_backtest_contracts import BacktestOrderIntent, BacktestRunFacts
from app.domain.deterministic_backtest_runner_contracts import BacktestExecutionTrace, run_deterministic_backtest


class BacktestServiceError(ValueError):
    """Invalid backtest orchestration facts."""


@dataclass(frozen=True, slots=True)
class DeterministicBacktestService:
    """Run a supplied snapshot and optionally assemble its immutable report."""

    def execute(
        self,
        run: BacktestRunFacts,
        dataset: BacktestDatasetSnapshot,
        orders: tuple[BacktestOrderIntent, ...],
    ) -> BacktestExecutionTrace:
        if not isinstance(run, BacktestRunFacts) or not isinstance(dataset, BacktestDatasetSnapshot):
            raise BacktestServiceError("run and dataset must be typed")
        if run.dataset_snapshot_id != dataset.dataset_snapshot_id:
            raise BacktestServiceError("run and dataset snapshot do not match")
        return run_deterministic_backtest(run, dataset.bars, orders)

    def report(
        self,
        run: BacktestRunFacts,
        dataset: BacktestDatasetSnapshot,
        metrics: BacktestMetrics,
        *,
        walk_forward_windows: tuple[WalkForwardWindow, ...] = (),
        report_created_at: datetime,
    ) -> BacktestReportSnapshot:
        if not isinstance(run, BacktestRunFacts) or not isinstance(dataset, BacktestDatasetSnapshot):
            raise BacktestServiceError("run and dataset must be typed")
        if not isinstance(metrics, BacktestMetrics):
            raise BacktestServiceError("metrics must be typed")
        if run.dataset_snapshot_id != dataset.dataset_snapshot_id:
            raise BacktestServiceError("run and dataset snapshot do not match")
        return build_backtest_report(
            run,
            dataset,
            metrics,
            walk_forward_windows,
            report_created_at=report_created_at,
        )


__all__ = ["BacktestServiceError", "DeterministicBacktestService"]
