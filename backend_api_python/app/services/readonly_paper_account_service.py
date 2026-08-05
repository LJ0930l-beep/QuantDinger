"""Authenticated read-only PAPER account response boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.readonly_paper_account_contracts import ReadonlyPaperAccountSnapshot


class ReadonlyPaperAccountServiceError(RuntimeError):
    """The paper account provider is unavailable or unsafe."""


PaperAccountProvider = Callable[[int, int], Optional[ReadonlyPaperAccountSnapshot]]


@dataclass(frozen=True, slots=True)
class ReadonlyPaperAccountService:
    provider: Optional[PaperAccountProvider] = None

    def read_response(self, *, user_id: int, limit: int = 200, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyPaperAccountServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            value = self.provider(user_id, limit)
        except Exception as exc:
            raise ReadonlyPaperAccountServiceError("paper account provider failed") from exc
        if value is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(value, ReadonlyPaperAccountSnapshot):
            raise ReadonlyPaperAccountServiceError("provider returned invalid paper account facts")
        return 200, value.to_public_dict()


def service_from_app(app) -> ReadonlyPaperAccountService:
    return ReadonlyPaperAccountService(app.extensions.get("readonly_paper_account_provider"))


def postgres_paper_account_provider(user_id: int, limit: int) -> ReadonlyPaperAccountSnapshot | None:
    from app.utils.db import get_db_connection
    from app.services.readonly_paper_account_repository import ReadonlyPaperAccountRepository

    with get_db_connection() as connection:
        return ReadonlyPaperAccountRepository().read(connection, user_id=user_id, limit=limit)


__all__ = [
    "PaperAccountProvider",
    "ReadonlyPaperAccountService",
    "ReadonlyPaperAccountServiceError",
    "postgres_paper_account_provider",
    "service_from_app",
]
