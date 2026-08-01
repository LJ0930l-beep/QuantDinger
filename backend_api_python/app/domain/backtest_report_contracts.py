"""Immutable, replayable backtest report assembly.

The report is a read-only hand-off between deterministic dataset/metrics
reducers and a future API or UI.  It does not run a strategy, load data, or
place orders.  Every timestamp and policy fact is caller-owned and included in
the report fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .backtest_dataset_contracts import BacktestDatasetError, BacktestDatasetSnapshot
from .backtest_metrics_contracts import BacktestMetrics, BacktestMetricsError, WalkForwardWindow
from .deterministic_backtest_contracts import BacktestRunFacts, backtest_fingerprint


BACKTEST_REPORT_CONTRACT_VERSION = "backtest-report-v1"


class BacktestReportError(ValueError):
    """Incomplete or cross-scope backtest report facts."""


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise BacktestReportError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, (float, bool)) or not isinstance(value, Decimal) or not value.is_finite():
        raise BacktestReportError(f"{field} must be finite Decimal")
    return value


@dataclass(frozen=True, slots=True)
class BacktestReportSnapshot:
    run: BacktestRunFacts
    dataset: BacktestDatasetSnapshot
    metrics: BacktestMetrics
    walk_forward_windows: tuple[WalkForwardWindow, ...]
    report_created_at: datetime
    report_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, BacktestRunFacts) or not isinstance(self.dataset, BacktestDatasetSnapshot) or not isinstance(self.metrics, BacktestMetrics):
            raise BacktestReportError("run, dataset, and metrics must be typed")
        if self.run.dataset_snapshot_id != self.dataset.dataset_snapshot_id:
            raise BacktestReportError("run and dataset snapshot identity mismatch")
        if self.run.clock_start >= self.run.clock_end or self.run.clock_end > self.dataset.as_of:
            raise BacktestReportError("run clock must fit inside dataset as_of")
        windows = tuple(self.walk_forward_windows)
        if any(not isinstance(item, WalkForwardWindow) for item in windows):
            raise BacktestReportError("walk-forward windows must be typed")
        if any(item.start_at < self.run.clock_start or item.end_at > self.run.clock_end for item in windows):
            raise BacktestReportError("walk-forward window exceeds run clock")
        created = _utc(self.report_created_at, "report_created_at")
        for field_name in ("initial_equity", "final_equity", "total_return", "max_drawdown", "gross_pnl", "fees", "funding", "net_pnl", "win_rate"):
            _decimal(getattr(self.metrics, field_name), field_name)
        if self.metrics.sharpe_ratio is not None: _decimal(self.metrics.sharpe_ratio, "sharpe_ratio")
        if self.metrics.profit_factor is not None: _decimal(self.metrics.profit_factor, "profit_factor")
        object.__setattr__(self, "walk_forward_windows", windows)
        object.__setattr__(self, "report_created_at", created)
        object.__setattr__(self, "report_fingerprint", backtest_fingerprint(self.canonical_facts()))

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "version": BACKTEST_REPORT_CONTRACT_VERSION,
            "run": self.run,
            "dataset": self.dataset.dataset_fingerprint,
            "metrics": self.metrics,
            "walk_forward_windows": self.walk_forward_windows,
            "report_created_at": self.report_created_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        def decimal(value: Decimal | None) -> str | None:
            return None if value is None else format(value.normalize(), "f")

        return {
            "contract_version": BACKTEST_REPORT_CONTRACT_VERSION,
            "run_id": self.run.run_id,
            "dataset_snapshot_id": self.dataset.dataset_snapshot_id,
            "instrument_id": self.dataset.instrument_id,
            "valuation_ccy": self.run.valuation_ccy,
            "clock_start": self.run.clock_start.isoformat(),
            "clock_end": self.run.clock_end.isoformat(),
            "initial_equity": decimal(self.metrics.initial_equity),
            "final_equity": decimal(self.metrics.final_equity),
            "total_return": decimal(self.metrics.total_return),
            "max_drawdown": decimal(self.metrics.max_drawdown),
            "sharpe_ratio": decimal(self.metrics.sharpe_ratio),
            "gross_pnl": decimal(self.metrics.gross_pnl),
            "fees": decimal(self.metrics.fees),
            "funding": decimal(self.metrics.funding),
            "net_pnl": decimal(self.metrics.net_pnl),
            "walk_forward_window_count": len(self.walk_forward_windows),
            "report_fingerprint": self.report_fingerprint,
        }


def build_backtest_report(
    run: BacktestRunFacts,
    dataset: BacktestDatasetSnapshot,
    metrics: BacktestMetrics,
    walk_forward_windows: tuple[WalkForwardWindow, ...] = (),
    *,
    report_created_at: datetime,
) -> BacktestReportSnapshot:
    try:
        return BacktestReportSnapshot(run, dataset, metrics, walk_forward_windows, report_created_at)
    except (BacktestDatasetError, BacktestMetricsError, BacktestReportError):
        raise
    except Exception as exc:
        raise BacktestReportError("invalid backtest report facts") from exc


__all__ = ["BACKTEST_REPORT_CONTRACT_VERSION", "BacktestReportError", "BacktestReportSnapshot", "build_backtest_report"]
