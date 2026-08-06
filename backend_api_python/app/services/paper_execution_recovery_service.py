"""Caller-owned restart recovery for durable PAPER execution facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.services.paper_execution_account_service import read_durable_paper_account
from app.services.paper_execution_repository import PaperExecutionRepository, PaperExecutionRepositoryError


class PaperExecutionRecoveryError(RuntimeError):
    """PAPER recovery could not prove a complete durable snapshot."""


@dataclass(frozen=True, slots=True)
class PaperExecutionRecoveryResult:
    user_id: int
    checkpoint_version: int
    snapshot_fingerprint: str
    order_count: int
    recovered_at: datetime


def recover_durable_paper_account(connection: Any, *, user_id: int, limit: int = 200, recovered_at: datetime | None = None) -> PaperExecutionRecoveryResult:
    """Rebuild the Paper account and append a checkpoint in the caller transaction.

    The method deliberately never commits or rolls back.  A service startup can
    combine this checkpoint with any projection or audit writes and commit once.
    """

    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise PaperExecutionRecoveryError("user_id must be a positive integer")
    observed = recovered_at or datetime.now(timezone.utc)
    if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(observed):
        raise PaperExecutionRecoveryError("recovered_at must use zero UTC offset")
    observed = observed.astimezone(timezone.utc)
    try:
        snapshot = read_durable_paper_account(connection, user_id=user_id, limit=limit)
        repository = PaperExecutionRepository()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """SELECT checkpoint_version FROM qd_paper_recovery_checkpoints
                   WHERE user_id = %s ORDER BY checkpoint_version DESC LIMIT 1 FOR UPDATE""",
                (user_id,),
            )
            row = cursor.fetchone()
            version = (int(row[0] if not isinstance(row, dict) else row["checkpoint_version"]) + 1) if row is not None else 1
        finally:
            cursor.close()
        repository.record_recovery_checkpoint(
            connection,
            user_id=user_id,
            checkpoint_version=version,
            last_order_id=snapshot.orders[-1].order_uid if snapshot.orders else None,
            snapshot_fingerprint=snapshot.snapshot_fingerprint,
            status="READY",
        )
        return PaperExecutionRecoveryResult(user_id, version, snapshot.snapshot_fingerprint, len(snapshot.orders), observed)
    except PaperExecutionRecoveryError:
        raise
    except (PaperExecutionRepositoryError, ValueError, TypeError, KeyError) as exc:
        raise PaperExecutionRecoveryError("durable PAPER recovery failed") from exc
    except Exception as exc:
        raise PaperExecutionRecoveryError("durable PAPER recovery failed") from exc


__all__ = ["PaperExecutionRecoveryError", "PaperExecutionRecoveryResult", "recover_durable_paper_account"]
