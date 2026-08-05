"""Pure deterministic backtest metrics and evaluation-window contracts.

The module intentionally operates only on caller-owned facts.  It does not
load data, place orders, or read a portfolio.  Fees and funding remain
separate facts so a caller cannot accidentally hide costs in gross PnL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from app.domain.deterministic_backtest_contracts import BacktestContractError


BACKTEST_METRICS_CONTRACT_VERSION = "backtest-metrics-v1"


class BacktestMetricsError(BacktestContractError):
    """Raised for incomplete, non-deterministic, or invalid metric facts."""


def _decimal(value: Any, field_name: str, *, non_negative: bool = False) -> Decimal:
    if isinstance(value, (float, bool)):
        raise BacktestMetricsError(f"{field_name} rejects float/bool input")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BacktestMetricsError(f"{field_name} must be a decimal") from exc
    if not result.is_finite() or (non_negative and result < 0):
        raise BacktestMetricsError(f"{field_name} has invalid numeric bounds")
    return result


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise BacktestMetricsError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class BacktestEquityPoint:
    observed_at: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        object.__setattr__(self, "equity", _decimal(self.equity, "equity", non_negative=True))


@dataclass(frozen=True, slots=True)
class BacktestTradeResult:
    closed_at: datetime
    gross_pnl: Decimal
    fee: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "closed_at", _utc(self.closed_at, "closed_at"))
        object.__setattr__(self, "gross_pnl", _decimal(self.gross_pnl, "gross_pnl"))
        object.__setattr__(self, "fee", _decimal(self.fee, "fee", non_negative=True))
        object.__setattr__(self, "funding", _decimal(self.funding, "funding"))

    @property
    def net_pnl(self) -> Decimal:
        return self.gross_pnl - self.fee + self.funding


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    initial_equity: Decimal
    final_equity: Decimal
    total_return: Decimal
    max_drawdown: Decimal
    sharpe_ratio: Decimal | None
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal
    win_rate: Decimal
    profit_factor: Decimal | None


def calculate_backtest_metrics(
    equity_points: Iterable[BacktestEquityPoint],
    trades: Iterable[BacktestTradeResult] = (),
    *,
    periods_per_year: int = 365,
) -> BacktestMetrics:
    """Calculate deterministic equity/PnL metrics from an ordered fact set.

    Points must be strictly chronological; returns are simple period returns.
    Sharpe is annualised with Decimal arithmetic and is unavailable for fewer
    than two non-zero observations.  No implicit zero or guessed value is used.
    """
    points = tuple(equity_points)
    if len(points) < 2 or any(not isinstance(item, BacktestEquityPoint) for item in points):
        raise BacktestMetricsError("at least two typed equity points are required")
    if any(left.observed_at >= right.observed_at for left, right in zip(points, points[1:])):
        raise BacktestMetricsError("equity points must be strictly chronological")
    if isinstance(periods_per_year, bool) or not isinstance(periods_per_year, int) or periods_per_year <= 0:
        raise BacktestMetricsError("periods_per_year must be a positive integer")
    results = tuple(trades)
    if any(not isinstance(item, BacktestTradeResult) for item in results):
        raise BacktestMetricsError("trades must use BacktestTradeResult")
    initial, final = points[0].equity, points[-1].equity
    if initial <= 0:
        raise BacktestMetricsError("initial equity must be positive")
    returns = tuple((current.equity - previous.equity) / previous.equity for previous, current in zip(points, points[1:]))
    mean = sum(returns, Decimal(0)) / Decimal(len(returns))
    variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns))
    sharpe = None if variance == 0 else (mean / variance.sqrt()) * Decimal(periods_per_year).sqrt()
    peak = initial
    max_dd = Decimal(0)
    for point in points:
        peak = max(peak, point.equity)
        max_dd = max(max_dd, (peak - point.equity) / peak)
    gross = sum((item.gross_pnl for item in results), Decimal(0))
    fees = sum((item.fee for item in results), Decimal(0))
    funding = sum((item.funding for item in results), Decimal(0))
    wins = sum(1 for item in results if item.net_pnl > 0)
    gains = sum((item.net_pnl for item in results if item.net_pnl > 0), Decimal(0))
    losses = sum((-item.net_pnl for item in results if item.net_pnl < 0), Decimal(0))
    return BacktestMetrics(initial, final, (final - initial) / initial, max_dd, sharpe,
                           gross, fees, funding, gross - fees + funding,
                           Decimal(wins) / Decimal(len(results)) if results else Decimal(0),
                           gains / losses if losses else None)


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    window_id: str
    start_at: datetime
    end_at: datetime
    out_of_sample: bool

    def __post_init__(self) -> None:
        if not isinstance(self.window_id, str) or not self.window_id or self.window_id.strip() != self.window_id:
            raise BacktestMetricsError("window_id must be canonical text")
        start, end = _utc(self.start_at, "start_at"), _utc(self.end_at, "end_at")
        if end <= start:
            raise BacktestMetricsError("window end must follow start")
        if not isinstance(self.out_of_sample, bool):
            raise BacktestMetricsError("out_of_sample must be boolean")
        object.__setattr__(self, "start_at", start); object.__setattr__(self, "end_at", end)


def build_walk_forward_windows(start_at: datetime, end_at: datetime, *, train_days: int, test_days: int) -> tuple[WalkForwardWindow, ...]:
    """Build non-overlapping train/test windows without peeking into the future."""
    start, end = _utc(start_at, "start_at"), _utc(end_at, "end_at")
    if train_days <= 0 or test_days <= 0 or end <= start:
        raise BacktestMetricsError("walk-forward bounds are invalid")
    from datetime import timedelta
    cursor, index, windows = start, 0, []
    while cursor + timedelta(days=train_days + test_days) <= end:
        train_end = cursor + timedelta(days=train_days)
        test_end = train_end + timedelta(days=test_days)
        windows.extend((WalkForwardWindow(f"w{index}-train", cursor, train_end, False), WalkForwardWindow(f"w{index}-test", train_end, test_end, True)))
        cursor, index = test_end, index + 1
    if not windows:
        raise BacktestMetricsError("date range is too short for one train/test window")
    return tuple(windows)


__all__ = ["BACKTEST_METRICS_CONTRACT_VERSION", "BacktestEquityPoint", "BacktestMetrics", "BacktestMetricsError", "BacktestTradeResult", "WalkForwardWindow", "build_walk_forward_windows", "calculate_backtest_metrics"]
