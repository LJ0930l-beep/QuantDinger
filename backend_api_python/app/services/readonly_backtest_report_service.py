"""Authenticated read-only service for canonical persisted backtest facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.backtest_report_contracts import BacktestReportSnapshot
from app.services.readonly_backtest_report_repository import ReadonlyBacktestReportRepository


class ReadonlyBacktestReportServiceError(RuntimeError):
    """The canonical report provider is unavailable or unsafe."""


ReportProvider = Callable[[int, int], Optional[BacktestReportSnapshot]]


@dataclass(frozen=True, slots=True)
class ReadonlyBacktestReportService:
    provider: Optional[ReportProvider] = None

    def read_response(self, *, user_id: int, run_id: int, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyBacktestReportServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            report = self.provider(user_id, run_id)
        except Exception as exc:
            raise ReadonlyBacktestReportServiceError("backtest report provider failed") from exc
        if report is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(report, BacktestReportSnapshot):
            raise ReadonlyBacktestReportServiceError("provider returned invalid backtest report")
        return 200, {**report.to_public_dict(), "live_enabled": False}


def service_from_app(app) -> ReadonlyBacktestReportService:
    return ReadonlyBacktestReportService(app.extensions.get("readonly_backtest_report_provider"))


def postgres_backtest_report_provider(user_id: int, run_id: int) -> BacktestReportSnapshot | None:
    from app.utils.db import get_db_connection

    with get_db_connection() as connection:
        return ReadonlyBacktestReportRepository().read(connection, user_id=user_id, run_id=run_id)


__all__ = [
    "ReadonlyBacktestReportService",
    "ReadonlyBacktestReportServiceError",
    "postgres_backtest_report_provider",
    "service_from_app",
]
