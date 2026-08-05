"""Read-only composition of Gate, backtest, and Paper/Shadow readiness facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.paper_shadow_run_result_contracts import PaperShadowRunStatus
from app.domain.research_readiness_contracts import ResearchReadinessError, ResearchReadinessView, derive_research_readiness


class ResearchReadinessServiceError(RuntimeError):
    """Injected readiness facts are unavailable or invalid."""


StatusProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class ResearchReadinessService:
    """Compose typed evidence without opening connections or enabling live mode."""

    gate_provider: Optional[StatusProvider] = None
    backtest_provider: Optional[StatusProvider] = None
    paper_shadow_provider: Optional[StatusProvider] = None

    def __post_init__(self) -> None:
        for provider in (self.gate_provider, self.backtest_provider, self.paper_shadow_provider):
            if provider is not None and not callable(provider):
                raise ResearchReadinessServiceError("readiness providers must be callable")

    def read_view(self, *, authorized: bool = True) -> ResearchReadinessView | None:
        if not isinstance(authorized, bool):
            raise ResearchReadinessServiceError("authorized must be boolean")
        if not authorized:
            return None
        if self.gate_provider is None or self.backtest_provider is None:
            return None
        try:
            gate = self.gate_provider()
            backtest = self.backtest_provider()
            paper = None if self.paper_shadow_provider is None else self.paper_shadow_provider()
            if paper is not None and not isinstance(paper, PaperShadowRunStatus):
                raise ResearchReadinessError("paper provider returned invalid status")
            return derive_research_readiness(gate, backtest, paper, live_enabled=False)
        except ResearchReadinessError as exc:
            raise ResearchReadinessServiceError("readiness providers returned invalid facts") from exc
        except Exception as exc:
            raise ResearchReadinessServiceError("readiness provider failed") from exc

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not authorized:
            return 401, {"contract_version": "research-readiness-v1", "status": "UNAUTHORIZED", "live_enabled": False}
        view = self.read_view(authorized=authorized)
        if view is None:
            return 503, {"contract_version": "research-readiness-v1", "status": "UNAVAILABLE", "live_enabled": False}
        return 200, view.to_public_dict()


def service_from_app(app) -> ResearchReadinessService:
    extensions = app.extensions
    return ResearchReadinessService(
        extensions.get("readonly_gate_readiness_provider"),
        extensions.get("readonly_backtest_status_provider"),
        extensions.get("readonly_paper_shadow_status_provider"),
    )


__all__ = ["ResearchReadinessService", "ResearchReadinessServiceError", "service_from_app"]
