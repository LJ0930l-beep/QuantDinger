"""Read-only adapter seam for deterministic backtest reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.backtest_report_contracts import BacktestReportSnapshot
from app.domain.backtest_result_contracts import (
    BacktestResultResponse,
    BacktestResultStatus,
    BacktestResultView,
    serialize_backtest_result,
)


class BacktestResultServiceError(RuntimeError):
    """A report provider cannot supply a typed, safe result."""


ReportProvider = Callable[[], Optional[BacktestReportSnapshot]]


@dataclass(frozen=True, slots=True)
class BacktestResultService:
    """Build a credential-free response from an injected report provider.

    The provider is deliberately caller-owned.  This service never opens a
    database connection, reads credentials, calls an exchange, or mutates a
    simulation.
    """

    report_provider: Optional[ReportProvider] = None

    def __post_init__(self) -> None:
        if self.report_provider is not None and not callable(self.report_provider):
            raise BacktestResultServiceError("report_provider must be callable")

    def read_view(self, *, authorized: bool = True) -> BacktestResultView:
        if not isinstance(authorized, bool):
            raise BacktestResultServiceError("authorized must be boolean")
        if not authorized:
            return BacktestResultView(BacktestResultStatus.UNAUTHORIZED)
        if self.report_provider is None:
            return BacktestResultView(BacktestResultStatus.UNAVAILABLE)
        try:
            report = self.report_provider()
        except Exception as exc:
            raise BacktestResultServiceError("backtest result provider failed") from exc
        if report is None:
            return BacktestResultView(BacktestResultStatus.UNAVAILABLE)
        if not isinstance(report, BacktestReportSnapshot):
            raise BacktestResultServiceError("provider returned invalid backtest facts")
        return BacktestResultView(BacktestResultStatus.READY, report)

    def read_response(self, *, authorized: bool = True) -> BacktestResultResponse:
        return serialize_backtest_result(self.read_view(authorized=authorized))


def service_from_app(app) -> BacktestResultService:
    """Resolve an explicitly injected provider; missing means unavailable."""

    provider = app.extensions.get("readonly_backtest_report_provider")
    if provider is not None and not callable(provider):
        raise BacktestResultServiceError("backtest result provider extension must be callable")
    return BacktestResultService(provider)


__all__ = [
    "BacktestResultService",
    "BacktestResultServiceError",
    "ReportProvider",
    "service_from_app",
]
