"""Read-only adapter seam for Paper/Shadow run summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.paper_shadow_run_result_contracts import (
    PaperShadowRunResult,
)


class PaperShadowResultServiceError(RuntimeError):
    """A result provider cannot supply typed simulation facts."""


ResultProvider = Callable[[], Optional[PaperShadowRunResult]]


@dataclass(frozen=True, slots=True)
class PaperShadowResultService:
    result_provider: Optional[ResultProvider] = None

    def __post_init__(self) -> None:
        if self.result_provider is not None and not callable(self.result_provider):
            raise PaperShadowResultServiceError("result_provider must be callable")

    def read_view(self, *, authorized: bool = True) -> dict:
        if not isinstance(authorized, bool):
            raise PaperShadowResultServiceError("authorized must be boolean")
        if not authorized:
            return {"status": "UNAUTHORIZED", "contract_version": "paper-shadow-result-api-v1"}
        if self.result_provider is None:
            return {"status": "UNAVAILABLE", "contract_version": "paper-shadow-result-api-v1"}
        try:
            result = self.result_provider()
        except Exception as exc:
            raise PaperShadowResultServiceError("paper/shadow result provider failed") from exc
        if result is None:
            return {"status": "UNAVAILABLE", "contract_version": "paper-shadow-result-api-v1"}
        if not isinstance(result, PaperShadowRunResult):
            raise PaperShadowResultServiceError("provider returned invalid paper/shadow facts")
        payload = result.to_public_dict()
        payload["api_contract_version"] = "paper-shadow-result-api-v1"
        return payload

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        view = self.read_view(authorized=authorized)
        status = view.get("status")
        return ({"READY": 200, "RUNNING": 200, "COMPLETED": 200, "FAILED": 200}.get(status, 401 if status == "UNAUTHORIZED" else 503), view)


def service_from_app(app) -> PaperShadowResultService:
    provider = app.extensions.get("readonly_paper_shadow_result_provider")
    if provider is not None and not callable(provider):
        raise PaperShadowResultServiceError("paper/shadow result provider extension must be callable")
    return PaperShadowResultService(provider)


__all__ = ["PaperShadowResultService", "PaperShadowResultServiceError", "ResultProvider", "service_from_app"]
