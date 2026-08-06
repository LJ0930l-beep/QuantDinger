"""Read-only API adapter for persisted projection generation summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from app.domain.readonly_projection_summary_contracts import ReadonlyProjectionGenerationSummary
from app.services.readonly_projection_repository import ReadonlyProjectionRepository


class ReadonlyProjectionSummaryServiceError(RuntimeError):
    """The projection summary provider is unavailable or unsafe."""


SummaryProvider = Callable[[str, datetime], Optional[ReadonlyProjectionGenerationSummary]]


@dataclass(frozen=True, slots=True)
class ReadonlyProjectionSummaryService:
    provider: Optional[SummaryProvider] = None

    def read_response(self, *, consumer_name: str, as_of: datetime, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyProjectionSummaryServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(consumer_name, str) or not consumer_name or consumer_name.strip() != consumer_name or not consumer_name.isascii():
            raise ReadonlyProjectionSummaryServiceError("consumer_name must be canonical ASCII text")
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timezone.utc.utcoffset(as_of):
            raise ReadonlyProjectionSummaryServiceError("as_of must use zero-offset UTC")
        try:
            value = self.provider(consumer_name, as_of.astimezone(timezone.utc))
        except Exception as exc:
            raise ReadonlyProjectionSummaryServiceError("projection summary provider failed") from exc
        if value is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(value, ReadonlyProjectionGenerationSummary):
            raise ReadonlyProjectionSummaryServiceError("provider returned invalid projection summary")
        return 200, value.to_public_dict()


def service_from_app(app) -> ReadonlyProjectionSummaryService:
    return ReadonlyProjectionSummaryService(app.extensions.get("readonly_projection_summary_provider"))


def postgres_projection_summary_provider(consumer_name: str, as_of: datetime) -> ReadonlyProjectionGenerationSummary | None:
    """Read the latest persisted generation using a short-lived SELECT-only connection."""

    from app.utils.db import get_db_connection

    with get_db_connection() as connection:
        return ReadonlyProjectionRepository().read_latest_generation(
            connection, consumer_name=consumer_name, as_of=as_of
        )


__all__ = [
    "ReadonlyProjectionSummaryService",
    "ReadonlyProjectionSummaryServiceError",
    "postgres_projection_summary_provider",
    "service_from_app",
]
