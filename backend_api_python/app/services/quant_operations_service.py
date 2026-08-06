"""Credential-free read adapter for the composed operating posture."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.quant_operations_contracts import QuantOperationsSnapshot


class QuantOperationsServiceError(RuntimeError):
    """Operational evidence is unavailable or not typed."""


OperationsProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class QuantOperationsService:
    provider: Optional[OperationsProvider] = None

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise QuantOperationsServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            snapshot = self.provider()
        except Exception as exc:
            raise QuantOperationsServiceError("operations provider failed") from exc
        if not isinstance(snapshot, QuantOperationsSnapshot):
            raise QuantOperationsServiceError("provider returned invalid operational facts")
        return 200, snapshot.to_public_dict()


def service_from_app(app) -> QuantOperationsService:
    return QuantOperationsService(app.extensions.get("readonly_quant_operations_provider"))


__all__ = ["QuantOperationsService", "QuantOperationsServiceError", "service_from_app"]
