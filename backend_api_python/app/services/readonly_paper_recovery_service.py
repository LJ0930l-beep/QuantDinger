"""Authenticated read-only Paper restart/recovery evidence API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.paper_recovery_contracts import (
    PaperRecoveryEvidence,
    PaperRecoveryError,
    verify_paper_snapshot_recovery,
)
from app.domain.readonly_paper_account_contracts import ReadonlyPaperAccountSnapshot


class ReadonlyPaperRecoveryServiceError(RuntimeError):
    """Paper recovery evidence is unavailable or malformed."""


PaperRecoveryProvider = Callable[[int, int], Optional[ReadonlyPaperAccountSnapshot]]


@dataclass(frozen=True, slots=True)
class ReadonlyPaperRecoveryService:
    provider: Optional[PaperRecoveryProvider] = None

    def read_response(
        self,
        *,
        user_id: int,
        limit: int = 200,
        expected_snapshot_fingerprint: str | None = None,
        authorized: bool = True,
    ) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ReadonlyPaperRecoveryServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            snapshot = self.provider(user_id, limit)
            if snapshot is None:
                return 503, {"status": "UNAVAILABLE", "live_enabled": False}
            evidence = verify_paper_snapshot_recovery(
                snapshot,
                expected_snapshot_fingerprint=expected_snapshot_fingerprint,
            )
            return 200, evidence.to_public_dict()
        except (PaperRecoveryError, TypeError, ValueError) as exc:
            raise ReadonlyPaperRecoveryServiceError("paper recovery evidence is invalid") from exc
        except Exception as exc:
            raise ReadonlyPaperRecoveryServiceError("paper recovery provider failed") from exc


def service_from_app(app) -> ReadonlyPaperRecoveryService:
    return ReadonlyPaperRecoveryService(app.extensions.get("readonly_paper_account_provider"))


def postgres_paper_recovery_provider(user_id: int, limit: int) -> ReadonlyPaperAccountSnapshot | None:
    from app.services.readonly_paper_account_service import postgres_paper_account_provider

    return postgres_paper_account_provider(user_id, limit)


__all__ = [
    "PaperRecoveryProvider",
    "ReadonlyPaperRecoveryService",
    "ReadonlyPaperRecoveryServiceError",
    "postgres_paper_recovery_provider",
    "service_from_app",
]
