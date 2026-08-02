"""Complete non-live research pipeline from bars to Paper/Shadow decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot
from app.domain.paper_shadow_contracts import PaperShadowDecision, PaperShadowRunFacts, simulation_fingerprint
from app.domain.portfolio_risk_contracts import CooldownFact, PositionSizingDecision, PositionSizingRequest, portfolio_risk_fingerprint
from app.domain.strategy_library_contracts import StrategyDefinition, StrategySignalFact, strategy_fingerprint
from app.services.paper_shadow_service import PaperShadowService
from app.services.portfolio_risk_service import PortfolioRiskService
from app.services.strategy_factory import StrategyFactory


@dataclass(frozen=True, slots=True)
class ResearchPipelineResult:
    """Typed evidence for one non-live research evaluation."""

    signal: StrategySignalFact
    sizing: PositionSizingDecision
    simulation: PaperShadowDecision
    result_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.signal, StrategySignalFact) or not isinstance(self.sizing, PositionSizingDecision) or not isinstance(self.simulation, PaperShadowDecision):
            raise ResearchPipelineError("pipeline result facts must be typed")
        if self.sizing.request_fingerprint != self.simulation.request_fingerprint:
            raise ResearchPipelineError("pipeline result request identity mismatch")
        if self.simulation.economic_fingerprint != self.signal.signal_id:
            raise ResearchPipelineError("pipeline result economic identity mismatch")
        object.__setattr__(self, "result_fingerprint", simulation_fingerprint({
            "version": "research-pipeline-result-v1",
            "signal": strategy_fingerprint(self.signal),
            "sizing": portfolio_risk_fingerprint(self.sizing),
            "simulation": simulation_fingerprint(self.simulation),
        }))

    def to_public_dict(self) -> dict[str, object]:
        """Return safe evidence for a read-only UI or report provider."""

        return {
            "contract_version": "research-pipeline-result-v1",
            "result_fingerprint": self.result_fingerprint,
            "signal": {
                "signal_id": self.signal.signal_id,
                "strategy_id": self.signal.strategy.strategy_id,
                "strategy_family": self.signal.strategy.family.value,
                "instrument_id": self.signal.instrument_id,
                "direction": self.signal.direction.value,
                "confidence": format(self.signal.confidence.normalize(), "f"),
                "occurred_at": self.signal.occurred_at.isoformat(),
                "data_snapshot_id": self.signal.data_snapshot_id,
            },
            "sizing": {
                "request_fingerprint": self.sizing.request_fingerprint,
                "disposition": self.sizing.disposition.value,
                "approved_quantity": format(self.sizing.approved_quantity.normalize(), "f"),
                "notional": format(self.sizing.notional.normalize(), "f"),
                "required_margin": format(self.sizing.required_margin.normalize(), "f"),
                "reason": self.sizing.reason,
            },
            "simulation": {
                "run_id": self.simulation.run_id,
                "mode": self.simulation.mode.value,
                "disposition": self.simulation.disposition.value,
                "quantity": format(self.simulation.quantity.normalize(), "f"),
                "notional": format(self.simulation.notional.normalize(), "f"),
                "reason": self.simulation.reason,
                "decided_at": self.simulation.decided_at.isoformat(),
            },
        }


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
        cooldown: CooldownFact | None = None,
        now: datetime | None = None,
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
        sizing = self.risk_service.evaluate(sizing_request, cooldown=cooldown, now=now)
        simulation = self.simulation_service.decide(
            run,
            signal,
            sizing,
            request_fingerprint=request_fingerprint,
            decided_at=decided_at,
        )
        return ResearchPipelineResult(signal, sizing, simulation)

    def evaluate_dataset(
        self,
        definition: StrategyDefinition,
        dataset: BacktestDatasetSnapshot,
        sizing_request: PositionSizingRequest,
        run: PaperShadowRunFacts,
        *,
        signal_id: str,
        request_fingerprint: str,
        decided_at: datetime,
        cooldown: CooldownFact | None = None,
        now: datetime | None = None,
    ) -> ResearchPipelineResult:
        """Run the same pipeline from a complete point-in-time dataset.

        The dataset owns the snapshot identity and quality proof, so callers
        cannot silently pass a different bar snapshot under the same run.
        """

        if not isinstance(dataset, BacktestDatasetSnapshot):
            raise ResearchPipelineError("dataset must be typed")
        if run.dataset_snapshot_id != dataset.dataset_snapshot_id:
            raise ResearchPipelineError("run dataset snapshot does not match dataset")
        return self.evaluate(
            definition,
            dataset.bars,
            sizing_request,
            run,
            signal_id=signal_id,
            data_snapshot_id=dataset.dataset_snapshot_id,
            request_fingerprint=request_fingerprint,
            decided_at=decided_at,
            cooldown=cooldown,
            now=now,
        )


__all__ = ["ResearchPipeline", "ResearchPipelineError", "ResearchPipelineResult"]
