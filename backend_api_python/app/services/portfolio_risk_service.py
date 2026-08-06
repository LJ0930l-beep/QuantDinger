"""Composition of deterministic sizing and cooldown risk facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.portfolio_risk_contracts import (
    CooldownFact,
    CooldownState,
    PositionSizingDecision,
    PositionSizingRequest,
    SizingDisposition,
    evaluate_position_sizing,
)


class PortfolioRiskServiceError(ValueError):
    """Invalid risk composition input."""


@dataclass(frozen=True, slots=True)
class PortfolioRiskService:
    """Apply cooldown before deterministic position sizing."""

    def evaluate(
        self,
        request: PositionSizingRequest,
        *,
        cooldown: CooldownFact | None = None,
        now: datetime | None = None,
    ) -> PositionSizingDecision:
        if not isinstance(request, PositionSizingRequest):
            raise PortfolioRiskServiceError("request must be typed")
        if cooldown is not None and not isinstance(cooldown, CooldownFact):
            raise PortfolioRiskServiceError("cooldown must be typed")
        if cooldown is not None and cooldown.state is CooldownState.ACTIVE:
            if now is None:
                raise PortfolioRiskServiceError("now is required for active cooldown")
            if not isinstance(now, datetime) or cooldown.until is None:
                raise PortfolioRiskServiceError("active cooldown facts are incomplete")
            if now.astimezone(cooldown.until.tzinfo) < cooldown.until:
                return PositionSizingDecision(
                    request.request_fingerprint,
                    SizingDisposition.DENIED,
                    0,
                    0,
                    0,
                    "cooldown_active",
                )
        return evaluate_position_sizing(request)


__all__ = ["PortfolioRiskService", "PortfolioRiskServiceError"]
