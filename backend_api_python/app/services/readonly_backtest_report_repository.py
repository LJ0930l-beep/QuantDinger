"""SELECT-only reader for canonical, persisted backtest reports."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.domain.backtest_report_codec import BacktestReportCodecError, deserialize_backtest_report
from app.domain.backtest_report_contracts import BacktestReportSnapshot


class ReadonlyBacktestReportRepositoryError(RuntimeError):
    """The database row is unavailable, legacy, or not canonical."""


def _row(row: Any, index: int, key: str) -> Any:
    try:
        return row[key] if isinstance(row, dict) else row[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReadonlyBacktestReportRepositoryError("database returned an incomplete backtest row") from exc


class ReadonlyBacktestReportRepository:
    """Read one user-scoped canonical report without transaction control."""

    def read(self, connection: Any, *, user_id: int, run_id: int) -> BacktestReportSnapshot | None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ReadonlyBacktestReportRepositoryError("user_id must be a positive integer")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise ReadonlyBacktestReportRepositoryError("run_id must be a positive integer")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT id, user_id, status, result_json, created_at
                  FROM qd_backtest_runs
                 WHERE id = %s AND user_id = %s
                 LIMIT 1
                """,
                (run_id, user_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            if int(_row(row, 1, "user_id")) != user_id or str(_row(row, 2, "status")) != "success":
                return None
            raw = _row(row, 3, "result_json")
            if not isinstance(raw, str) or not raw:
                raise ReadonlyBacktestReportRepositoryError("backtest row is not a canonical report")
            try:
                payload = json.loads(raw)
                return deserialize_backtest_report(payload)
            except (json.JSONDecodeError, TypeError, ValueError, BacktestReportCodecError) as exc:
                raise ReadonlyBacktestReportRepositoryError("backtest row is not a canonical report") from exc
        except ReadonlyBacktestReportRepositoryError:
            raise
        except Exception as exc:
            raise ReadonlyBacktestReportRepositoryError("backtest report read failed") from exc
        finally:
            cursor.close()


__all__ = ["ReadonlyBacktestReportRepository", "ReadonlyBacktestReportRepositoryError"]
