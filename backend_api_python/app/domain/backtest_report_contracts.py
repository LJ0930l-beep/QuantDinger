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


def _canonical_fingerprint(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise BacktestReportError(f"{field_name} must be a lowercase sha256 fingerprint")
    return value


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise BacktestReportError(f"{field} must be zero-offset UTC")
    return value.astimezone(timezone.utc)


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, (float, bool)) or not isinstance(value, Decimal) or not value.is_finite():
        raise BacktestReportError(f"{field} must be finite Decimal")
    return value


@dataclass(frozen=True, slots=True)
class BacktestExecutionEvidence:
    """Immutable execution facts attached to a deterministic report.

    The report metrics remain caller-owned.  This optional block binds the
    report to the exact execution trace and preserves multi-asset fees without
    inventing a scalar conversion.  It contains no venue or credential data.
    """

    execution_trace_fingerprint: str
    cost_trace_fingerprint: str | None = None
    portfolio_state_fingerprint: str | None = None
    valuation_ccy: str = ""
    fees_by_asset: tuple[tuple[str, Decimal], ...] = ()
    funding: Decimal = Decimal("0")
    applied_fill_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _canonical_fingerprint(self.execution_trace_fingerprint, "execution_trace_fingerprint")
        _canonical_fingerprint(self.cost_trace_fingerprint, "cost_trace_fingerprint", optional=True)
        _canonical_fingerprint(self.portfolio_state_fingerprint, "portfolio_state_fingerprint", optional=True)
        if not isinstance(self.valuation_ccy, str) or not self.valuation_ccy or self.valuation_ccy != self.valuation_ccy.upper() or not self.valuation_ccy.isascii() or any(char.isspace() for char in self.valuation_ccy):
            raise BacktestReportError("valuation_ccy must be canonical uppercase text")
        if not isinstance(self.fees_by_asset, tuple):
            raise BacktestReportError("fees_by_asset must be a tuple")
        normalized: list[tuple[str, Decimal]] = []
        for item in self.fees_by_asset:
            if not isinstance(item, tuple) or len(item) != 2:
                raise BacktestReportError("fees_by_asset must contain asset/value pairs")
            asset, amount = item
            if not isinstance(asset, str) or not asset or asset != asset.upper() or not asset.isascii() or any(char.isspace() for char in asset):
                raise BacktestReportError("fee asset must be canonical uppercase text")
            if not isinstance(amount, Decimal) or not amount.is_finite() or amount < 0:
                raise BacktestReportError("fee amount must be a non-negative Decimal")
            normalized.append((asset, amount))
        if tuple(sorted(normalized)) != tuple(normalized) or len({asset for asset, _ in normalized}) != len(normalized):
            raise BacktestReportError("fees_by_asset must be sorted and unique")
        if not isinstance(self.funding, Decimal) or not self.funding.is_finite():
            raise BacktestReportError("funding must be a finite Decimal")
        if not isinstance(self.applied_fill_ids, tuple) or any(not isinstance(item, str) or not item or item.strip() != item or not item.isascii() for item in self.applied_fill_ids):
            raise BacktestReportError("applied_fill_ids must be canonical text")
        if len(set(self.applied_fill_ids)) != len(self.applied_fill_ids):
            raise BacktestReportError("applied_fill_ids must be unique")
        object.__setattr__(self, "fees_by_asset", tuple((asset, amount) for asset, amount in normalized))

    def canonical_facts(self) -> dict[str, Any]:
        return {
            "execution_trace_fingerprint": self.execution_trace_fingerprint,
            "cost_trace_fingerprint": self.cost_trace_fingerprint,
            "portfolio_state_fingerprint": self.portfolio_state_fingerprint,
            "valuation_ccy": self.valuation_ccy,
            "fees_by_asset": [(asset, format(amount.normalize(), "f")) for asset, amount in self.fees_by_asset],
            "funding": format(self.funding.normalize(), "f"),
            "applied_fill_ids": list(self.applied_fill_ids),
        }


@dataclass(frozen=True, slots=True)
class BacktestReportSnapshot:
    run: BacktestRunFacts
    dataset: BacktestDatasetSnapshot
    metrics: BacktestMetrics
    walk_forward_windows: tuple[WalkForwardWindow, ...]
    report_created_at: datetime
    execution_evidence: BacktestExecutionEvidence | None = None
    report_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, BacktestRunFacts) or not isinstance(self.dataset, BacktestDatasetSnapshot) or not isinstance(self.metrics, BacktestMetrics):
            raise BacktestReportError("run, dataset, and metrics must be typed")
        if self.execution_evidence is not None and not isinstance(self.execution_evidence, BacktestExecutionEvidence):
            raise BacktestReportError("execution_evidence must be typed")
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
            "execution_evidence": None if self.execution_evidence is None else self.execution_evidence.canonical_facts(),
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
            "execution_evidence": None if self.execution_evidence is None else {
                "execution_trace_fingerprint": self.execution_evidence.execution_trace_fingerprint,
                "cost_trace_fingerprint": self.execution_evidence.cost_trace_fingerprint,
                "portfolio_state_fingerprint": self.execution_evidence.portfolio_state_fingerprint,
                "valuation_ccy": self.execution_evidence.valuation_ccy,
                "fees_by_asset": [
                    {"asset": asset, "amount": decimal(amount)}
                    for asset, amount in self.execution_evidence.fees_by_asset
                ],
                "funding": decimal(self.execution_evidence.funding),
                "applied_fill_ids": list(self.execution_evidence.applied_fill_ids),
            },
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
    execution_evidence: BacktestExecutionEvidence | None = None,
) -> BacktestReportSnapshot:
    try:
        return BacktestReportSnapshot(run, dataset, metrics, walk_forward_windows, report_created_at, execution_evidence)
    except (BacktestDatasetError, BacktestMetricsError, BacktestReportError):
        raise
    except Exception as exc:
        raise BacktestReportError("invalid backtest report facts") from exc


__all__ = ["BACKTEST_REPORT_CONTRACT_VERSION", "BacktestExecutionEvidence", "BacktestReportError", "BacktestReportSnapshot", "build_backtest_report"]
