"""Read-only API adapter for Gate TestNet rehearsal evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.gate_testnet_rehearsal_contracts import GateTestnetRehearsalResult


class GateTestnetRehearsalResultServiceError(RuntimeError):
    """Rehearsal evidence is unavailable or invalid."""


RehearsalProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class GateTestnetRehearsalResultService:
    provider: Optional[RehearsalProvider] = None

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise GateTestnetRehearsalResultServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            result = self.provider()
        except Exception as exc:
            raise GateTestnetRehearsalResultServiceError("rehearsal provider failed") from exc
        if not isinstance(result, GateTestnetRehearsalResult):
            raise GateTestnetRehearsalResultServiceError("provider returned invalid rehearsal facts")
        return 200, result.to_public_dict()


def service_from_app(app) -> GateTestnetRehearsalResultService:
    return GateTestnetRehearsalResultService(app.extensions.get("readonly_gate_testnet_rehearsal_provider"))


__all__ = ["GateTestnetRehearsalResultService", "GateTestnetRehearsalResultServiceError", "service_from_app"]
