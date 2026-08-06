"""Read-only adapter for authenticated, scope-bound reconciliation facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.domain.readonly_reconciliation_summary_contracts import ReadonlyReconciliationCheckpointSummary
from app.services.readonly_reconciliation_repository import ReadonlyReconciliationRepository


class ReadonlyReconciliationSummaryServiceError(RuntimeError):
    """The reconciliation provider is unavailable or returned unsafe facts."""


SummaryProvider = Callable[[int, int, str, str, str, str, datetime], Optional[ReadonlyReconciliationCheckpointSummary]]


@dataclass(frozen=True, slots=True)
class ReadonlyReconciliationSummaryService:
    provider: Optional[SummaryProvider] = None

    def read_response(self, *, user_id: int, credential_id: int, exchange: str, market_type: str, account_scope: str, instrument_id: str, as_of: datetime, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyReconciliationSummaryServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyReconciliationSummaryServiceError("as_of must use zero-offset UTC")
        try:
            value = self.provider(user_id, credential_id, exchange, market_type, account_scope, instrument_id, as_of.astimezone(timezone.utc))
        except Exception as exc:
            raise ReadonlyReconciliationSummaryServiceError("reconciliation summary provider failed") from exc
        if value is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(value, ReadonlyReconciliationCheckpointSummary):
            raise ReadonlyReconciliationSummaryServiceError("provider returned invalid reconciliation summary")
        return 200, value.to_public_dict()


def service_from_app(app) -> ReadonlyReconciliationSummaryService:
    return ReadonlyReconciliationSummaryService(app.extensions.get("readonly_reconciliation_summary_provider"))


def postgres_reconciliation_summary_provider(user_id: int, credential_id: int, exchange: str, market_type: str, account_scope: str, instrument_id: str, as_of: datetime) -> ReadonlyReconciliationCheckpointSummary | None:
    from app.utils.db import get_db_connection

    with get_db_connection() as connection:
        return ReadonlyReconciliationRepository().read_checkpoint(connection, user_id=user_id, credential_id=credential_id, exchange=exchange, market_type=market_type, account_scope=account_scope, instrument_id=instrument_id, as_of=as_of)


__all__ = ["ReadonlyReconciliationSummaryService", "ReadonlyReconciliationSummaryServiceError", "postgres_reconciliation_summary_provider", "service_from_app"]
