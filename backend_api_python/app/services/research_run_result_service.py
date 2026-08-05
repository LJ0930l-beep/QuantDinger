"""Read-only API adapter for a completed Gate research run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.services.gate_research_run_service import GateResearchRunResult


class ResearchRunResultServiceError(RuntimeError):
    """The injected research run result is unavailable or invalid."""


RunProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class ResearchRunResultService:
    provider: Optional[RunProvider] = None

    def __post_init__(self) -> None:
        if self.provider is not None and not callable(self.provider):
            raise ResearchRunResultServiceError("run provider must be callable")

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ResearchRunResultServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"contract_version": "gate-research-run-v1", "status": "UNAUTHORIZED", "live_enabled": False}
        if self.provider is None:
            return 503, {"contract_version": "gate-research-run-v1", "status": "UNAVAILABLE", "live_enabled": False}
        try:
            result = self.provider()
        except Exception as exc:
            raise ResearchRunResultServiceError("research run provider failed") from exc
        if result is None:
            return 503, {"contract_version": "gate-research-run-v1", "status": "UNAVAILABLE", "live_enabled": False}
        if not isinstance(result, GateResearchRunResult):
            raise ResearchRunResultServiceError("provider returned invalid research run facts")
        return 200, result.to_public_dict()


def service_from_app(app) -> ResearchRunResultService:
    return ResearchRunResultService(app.extensions.get("readonly_gate_research_run_provider"))


__all__ = ["ResearchRunResultService", "ResearchRunResultServiceError", "service_from_app"]
