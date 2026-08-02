"""SELECT-only, scope-bound reconciliation checkpoint repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.readonly_reconciliation_summary_contracts import (
    ReadonlyReconciliationCheckpointSummary,
    ReadonlyReconciliationSummaryError,
)


class ReadonlyReconciliationRepositoryError(RuntimeError):
    """The database did not provide a safe scoped checkpoint fact."""


def _row(row: Any, index: int, key: str) -> Any:
    try:
        return row[key] if isinstance(row, dict) else row[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReadonlyReconciliationRepositoryError("database returned an incomplete reconciliation row") from exc


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyReconciliationRepositoryError(f"database {field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


class ReadonlyReconciliationRepository:
    """Read one checkpoint for one authenticated credential/scope; never controls transactions."""

    def read_checkpoint(
        self,
        connection: Any,
        *,
        user_id: int,
        credential_id: int,
        exchange: str,
        market_type: str,
        account_scope: str,
        instrument_id: str,
        as_of: datetime,
    ) -> ReadonlyReconciliationCheckpointSummary | None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ReadonlyReconciliationRepositoryError("user_id must be a positive integer")
        if isinstance(credential_id, bool) or not isinstance(credential_id, int) or credential_id <= 0:
            raise ReadonlyReconciliationRepositoryError("credential_id must be a positive integer")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyReconciliationRepositoryError("as_of must use zero-offset UTC")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT r.id, r.credential_id, r.exchange, r.market_type,
                       r.account_scope, r.instrument_id, r.status,
                       r.unresolved_count, r.version, r.last_success_at,
                       r.sla_deadline, r.updated_at
                  FROM qd_reconciliation_checkpoints r
                  JOIN qd_exchange_credentials c ON c.id = r.credential_id
                 WHERE c.user_id = %s
                   AND r.credential_id = %s
                   AND r.exchange = %s
                   AND r.market_type = %s
                   AND r.account_scope = %s
                   AND r.instrument_id = %s
                 LIMIT 1
                """,
                (user_id, credential_id, exchange, market_type, account_scope, instrument_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            try:
                return ReadonlyReconciliationCheckpointSummary(
                    checkpoint_id=_row(row, 0, "id"),
                    credential_id=int(_row(row, 1, "credential_id")),
                    exchange=_row(row, 2, "exchange"),
                    market_type=_row(row, 3, "market_type"),
                    account_scope=_row(row, 4, "account_scope"),
                    instrument_id=_row(row, 5, "instrument_id"),
                    status=_row(row, 6, "status"),
                    unresolved_count=int(_row(row, 7, "unresolved_count")),
                    version=int(_row(row, 8, "version")),
                    last_success_at=None if _row(row, 9, "last_success_at") is None else _utc(_row(row, 9, "last_success_at"), "last_success_at"),
                    sla_deadline=None if _row(row, 10, "sla_deadline") is None else _utc(_row(row, 10, "sla_deadline"), "sla_deadline"),
                    updated_at=_utc(_row(row, 11, "updated_at"), "updated_at"),
                    as_of=as_of.astimezone(timezone.utc),
                )
            except (ReadonlyReconciliationSummaryError, ValueError, TypeError) as exc:
                raise ReadonlyReconciliationRepositoryError("database returned invalid reconciliation facts") from exc
        except ReadonlyReconciliationRepositoryError:
            raise
        except Exception as exc:
            raise ReadonlyReconciliationRepositoryError("reconciliation read failed") from exc
        finally:
            cursor.close()


__all__ = ["ReadonlyReconciliationRepository", "ReadonlyReconciliationRepositoryError"]
