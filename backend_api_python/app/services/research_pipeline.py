"""Complete non-live research pipeline from bars to Paper/Shadow decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.paper_shadow_contracts import PaperShadowDecision, PaperShadowRunFacts
from app.domain.portfolio_risk_contracts import PositionSizingDecision, PositionSizingRequest
from app.domain.strategy_library_contracts import StrategyDefinition, StrategySignalFact
from app.services.paper_shadow_service import PaperShadowService
from app.services.portfolio_risk_service import PortfolioRiskService
from app.services.strategy_factory import StrategyFactory


@dataclass(frozen=True, slots=True)
class ResearchPipelineResult:
    """Typed evidence for one non-live research evaluation."""

    signal: StrategySignalFact
    sizing: PositionSizingDecision
    simulation: PaperShadowDecision


class ResearchPipelineError(ValueError):
    """The supplied research facts cannot form a safe result."""


@dataclass(frozen=True, slots=True)
class ResearchPipeline:
    strategy_factory: StrategyFactory = StrategyFactory()
    risk_service: PortfolioRiskService = PortfolioRiskService()
    simulation_service: PaperShadowService = PaperShadowService()

    def evaluate(
        self,
        definition: StrategyDefinition,
        bars: Iterable[BacktestBar],
        sizing_request: PositionSizingRequest,
        run: PaperShadowRunFacts,
        *,
        signal_id: str,
        data_snapshot_id: str,
        request_fingerprint: str,
        decided_at: datetime,
    ) -> ResearchPipelineResult:
        if not isinstance(definition, StrategyDefinition):
            raise ResearchPipelineError("definition must be typed")
        if not isinstance(sizing_request, PositionSizingRequest):
            raise ResearchPipelineError("sizing_request must be typed")
        if not isinstance(run, PaperShadowRunFacts):
            raise ResearchPipelineError("run must be typed")
        signal = self.strategy_factory.generate_signal(
            definition,
            tuple(bars),
            signal_id=signal_id,
            data_snapshot_id=data_snapshot_id,
        )
        sizing = self.risk_service.evaluate(sizing_request)
        simulation = self.simulation_service.decide(
            run,
            signal,
            sizing,
            request_fingerprint=request_fingerprint,
            decided_at=decided_at,
        )
        return ResearchPipelineResult(signal, sizing, simulation)


__all__ = ["ResearchPipeline", "ResearchPipelineError", "ResearchPipelineResult"]
