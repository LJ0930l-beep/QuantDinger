"""SELECT-only, authenticated scope-bound Shadow Diff summary repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.domain.readonly_shadow_summary_contracts import ReadonlyShadowComparisonSummary, ReadonlyShadowSummaryError


class ReadonlyShadowRepositoryError(RuntimeError):
    """The database did not provide a safe Shadow Diff summary."""


def _row(row: Any, index: int, key: str) -> Any:
    try:
        return row[key] if isinstance(row, dict) else row[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise ReadonlyShadowRepositoryError("database returned an incomplete shadow summary row") from exc


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ReadonlyShadowRepositoryError(f"database {field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


class ReadonlyShadowRepository:
    """Read the latest COMPLETE run only; never owns the transaction."""

    def read_latest(
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
    ) -> ReadonlyShadowComparisonSummary | None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ReadonlyShadowRepositoryError("user_id must be a positive integer")
        if isinstance(credential_id, bool) or not isinstance(credential_id, int) or credential_id <= 0:
            raise ReadonlyShadowRepositoryError("credential_id must be a positive integer")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyShadowRepositoryError("as_of must use zero-offset UTC")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT r.id, r.credential_id, r.exchange, r.market_type, r.account_scope,
                       r.instrument_id, r.candidate_generation_id, r.candidate_consumer_name,
                       r.candidate_generation_build_fingerprint, r.candidate_checkpoint_watermark,
                       r.as_of, r.tolerance_policy_version, r.quantity_absolute, r.quantity_relative,
                       r.monetary_absolute, r.monetary_relative, r.ratio_absolute,
                       r.tolerance_policy_fingerprint, r.build_fingerprint, r.replay_fingerprint,
                       r.completed_at, COUNT(f.id),
                       COALESCE(SUM(CASE WHEN f.severity = 'BLOCKING' THEN 1 ELSE 0 END), 0)
                  FROM qd_shadow_comparison_runs r
                  JOIN qd_exchange_credentials c ON c.id = r.credential_id
             LEFT JOIN qd_shadow_diff_facts f ON f.run_id = r.id
                 WHERE c.user_id = %s
                   AND r.credential_id = %s
                   AND r.exchange = %s
                   AND r.market_type = %s
                   AND r.account_scope = %s
                   AND r.instrument_id = %s
                   AND r.state = 'COMPLETE'
                   AND r.as_of <= %s
              GROUP BY r.id, r.credential_id, r.exchange, r.market_type, r.account_scope,
                       r.instrument_id, r.candidate_generation_id, r.candidate_consumer_name,
                       r.candidate_generation_build_fingerprint, r.candidate_checkpoint_watermark,
                       r.as_of, r.tolerance_policy_version, r.quantity_absolute, r.quantity_relative,
                       r.monetary_absolute, r.monetary_relative, r.ratio_absolute,
                       r.tolerance_policy_fingerprint, r.build_fingerprint, r.replay_fingerprint,
                       r.completed_at
              ORDER BY r.completed_at DESC
                 LIMIT 1
                """,
                (user_id, credential_id, exchange, market_type, account_scope, instrument_id, as_of.astimezone(timezone.utc)),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            try:
                return ReadonlyShadowComparisonSummary(
                    run_id=_row(row, 0, "id"), credential_id=int(_row(row, 1, "credential_id")),
                    exchange=_row(row, 2, "exchange"), market_type=_row(row, 3, "market_type"),
                    account_scope=_row(row, 4, "account_scope"), instrument_id=_row(row, 5, "instrument_id"),
                    candidate_generation_id=_row(row, 6, "candidate_generation_id"), candidate_consumer_name=_row(row, 7, "candidate_consumer_name"),
                    candidate_generation_build_fingerprint=_row(row, 8, "candidate_generation_build_fingerprint"), candidate_checkpoint_watermark=int(_row(row, 9, "candidate_checkpoint_watermark")),
                    as_of=_utc(_row(row, 10, "as_of"), "as_of"), tolerance_policy_version=_row(row, 11, "tolerance_policy_version"),
                    quantity_absolute=_row(row, 12, "quantity_absolute"), quantity_relative=_row(row, 13, "quantity_relative"),
                    monetary_absolute=_row(row, 14, "monetary_absolute"), monetary_relative=_row(row, 15, "monetary_relative"),
                    ratio_absolute=_row(row, 16, "ratio_absolute"), tolerance_policy_fingerprint=_row(row, 17, "tolerance_policy_fingerprint"),
                    build_fingerprint=_row(row, 18, "build_fingerprint"), replay_fingerprint=_row(row, 19, "replay_fingerprint"),
                    completed_at=_utc(_row(row, 20, "completed_at"), "completed_at"), diff_count=int(_row(row, 21, "diff_count")), blocking_diff_count=int(_row(row, 22, "blocking_diff_count")),
                )
            except (ReadonlyShadowSummaryError, ValueError, TypeError) as exc:
                raise ReadonlyShadowRepositoryError("database returned invalid shadow summary facts") from exc
        except ReadonlyShadowRepositoryError:
            raise
        except Exception as exc:
            raise ReadonlyShadowRepositoryError("shadow summary read failed") from exc
        finally:
            cursor.close()


__all__ = ["ReadonlyShadowRepository", "ReadonlyShadowRepositoryError"]
