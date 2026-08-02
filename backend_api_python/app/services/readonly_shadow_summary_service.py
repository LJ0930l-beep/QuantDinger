"""Read-only adapter for authenticated Shadow Diff summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.domain.readonly_shadow_summary_contracts import ReadonlyShadowComparisonSummary
from app.services.readonly_shadow_repository import ReadonlyShadowRepository


class ReadonlyShadowSummaryServiceError(RuntimeError):
    """The Shadow Diff provider is unavailable or returned unsafe facts."""


SummaryProvider = Callable[[int, int, str, str, str, str, datetime], Optional[ReadonlyShadowComparisonSummary]]


@dataclass(frozen=True, slots=True)
class ReadonlyShadowSummaryService:
    provider: Optional[SummaryProvider] = None

    def read_response(self, *, user_id: int, credential_id: int, exchange: str, market_type: str, account_scope: str, instrument_id: str, as_of: datetime, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyShadowSummaryServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyShadowSummaryServiceError("as_of must use zero-offset UTC")
        try:
            value = self.provider(user_id, credential_id, exchange, market_type, account_scope, instrument_id, as_of.astimezone(timezone.utc))
        except Exception as exc:
            raise ReadonlyShadowSummaryServiceError("shadow summary provider failed") from exc
        if value is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(value, ReadonlyShadowComparisonSummary):
            raise ReadonlyShadowSummaryServiceError("provider returned invalid shadow summary")
        return 200, value.to_public_dict()


def service_from_app(app) -> ReadonlyShadowSummaryService:
    return ReadonlyShadowSummaryService(app.extensions.get("readonly_shadow_summary_provider"))


def postgres_shadow_summary_provider(user_id: int, credential_id: int, exchange: str, market_type: str, account_scope: str, instrument_id: str, as_of: datetime) -> ReadonlyShadowComparisonSummary | None:
    from app.utils.db import get_db_connection

    with get_db_connection() as connection:
        return ReadonlyShadowRepository().read_latest(connection, user_id=user_id, credential_id=credential_id, exchange=exchange, market_type=market_type, account_scope=account_scope, instrument_id=instrument_id, as_of=as_of)


__all__ = ["ReadonlyShadowSummaryService", "ReadonlyShadowSummaryServiceError", "postgres_shadow_summary_provider", "service_from_app"]
