"""Paper/Shadow orchestration over deterministic, typed domain facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.paper_shadow_contracts import (
    PaperShadowDecision,
    PaperShadowRunFacts,
    SimulationDisposition,
    SimulationMode,
)
from app.domain.portfolio_risk_contracts import (
    PositionSizingDecision,
    SizingDisposition,
)
from app.domain.strategy_library_contracts import StrategySignalFact


class PaperShadowServiceError(ValueError):
    """Invalid simulation orchestration input."""


@dataclass(frozen=True, slots=True)
class PaperShadowService:
    """Create simulation decisions without submitting or persisting orders."""

    def decide(
        self,
        run: PaperShadowRunFacts,
        signal: StrategySignalFact,
        sizing: PositionSizingDecision,
        *,
        request_fingerprint: str,
        decided_at: datetime,
    ) -> PaperShadowDecision:
        if not isinstance(run, PaperShadowRunFacts):
            raise PaperShadowServiceError("run must be typed")
        if not isinstance(signal, StrategySignalFact):
            raise PaperShadowServiceError("signal must be typed")
        if not isinstance(sizing, PositionSizingDecision):
            raise PaperShadowServiceError("sizing must be typed")
        if not isinstance(request_fingerprint, str) or not request_fingerprint.strip():
            raise PaperShadowServiceError("request_fingerprint is required")
        if sizing.disposition is SizingDisposition.ALLOWED:
            disposition = SimulationDisposition.ACCEPTED
            reason = "risk_sizing_allowed"
        else:
            disposition = SimulationDisposition.REJECTED
            reason = f"risk_sizing_denied:{sizing.reason}"
        return PaperShadowDecision(
            run_id=run.run_id,
            request_fingerprint=request_fingerprint,
            economic_fingerprint=signal.signal_id,
            mode=run.mode,
            disposition=disposition,
            quantity=sizing.approved_quantity,
            notional=sizing.notional,
            reason=reason,
            decided_at=decided_at,
        )


__all__ = ["PaperShadowService", "PaperShadowServiceError"]
