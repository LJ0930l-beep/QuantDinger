"""SELECT-only reader for the existing durable PAPER order table."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.readonly_paper_account_contracts import (
    PaperOrderStatus,
    ReadonlyPaperAccountError,
    ReadonlyPaperAccountSnapshot,
    ReadonlyPaperOrderFact,
)


class ReadonlyPaperAccountRepositoryError(RuntimeError):
    """Paper account facts are unavailable or malformed."""


def _row(row: Any, index: int, key: str) -> Any:
    try:
        return row[key] if isinstance(row, dict) else row[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReadonlyPaperAccountRepositoryError("database returned an incomplete paper order row") from exc


def _utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ReadonlyPaperAccountRepositoryError("database created_at must use zero-offset UTC")
    # qd_agent_paper_orders predates timestamptz and stores UTC wall-clock
    # values in TIMESTAMP.  Normalize that legacy representation explicitly at
    # this read boundary; domain facts remain zero-offset UTC thereafter.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyPaperAccountRepositoryError("database created_at must use zero-offset UTC")
    return value.astimezone(timezone.utc)


class ReadonlyPaperAccountRepository:
    """Read paper orders without transaction control or write authority."""

    def read(self, connection: Any, *, user_id: int, limit: int = 200) -> ReadonlyPaperAccountSnapshot | None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ReadonlyPaperAccountRepositoryError("user_id must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ReadonlyPaperAccountRepositoryError("limit must be between 1 and 500")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT order_uid, market, symbol, side, order_type, qty,
                       limit_price, fill_price, fill_value, status, note, created_at
                  FROM qd_agent_paper_orders
                 WHERE user_id = %s
                 ORDER BY id DESC
                 LIMIT %s
                """,
                (user_id, limit),
            )
            rows = cursor.fetchall() or []
            orders = []
            for row in rows:
                try:
                    orders.append(ReadonlyPaperOrderFact(
                        order_uid=_row(row, 0, "order_uid"),
                        market=_row(row, 1, "market"),
                        symbol=_row(row, 2, "symbol"),
                        side=_row(row, 3, "side"),
                        order_type=_row(row, 4, "order_type"),
                        quantity=_row(row, 5, "qty"),
                        limit_price=_row(row, 6, "limit_price"),
                        fill_price=_row(row, 7, "fill_price"),
                        fill_value=_row(row, 8, "fill_value"),
                        status=PaperOrderStatus(str(_row(row, 9, "status")).lower()),
                        note=_row(row, 10, "note") or "",
                        created_at=_utc(_row(row, 11, "created_at")),
                    ))
                except (ReadonlyPaperAccountError, ValueError, TypeError) as exc:
                    raise ReadonlyPaperAccountRepositoryError("database returned invalid paper order facts") from exc
            observed_at = max((item.created_at for item in orders), default=datetime.now(timezone.utc))
            return ReadonlyPaperAccountSnapshot(user_id, tuple(orders), observed_at)
        except ReadonlyPaperAccountRepositoryError:
            raise
        except Exception as exc:
            raise ReadonlyPaperAccountRepositoryError("paper account read failed") from exc
        finally:
            cursor.close()


__all__ = ["ReadonlyPaperAccountRepository", "ReadonlyPaperAccountRepositoryError"]
