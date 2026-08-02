"""Compose Gate market facts, risk simulation, Paper/Shadow, and backtest.

The orchestrator is intentionally a read-only research boundary.  It reads a
caller-owned Gate session, builds one immutable dataset, then evaluates both
the existing risk/Paper/Shadow pipeline and the deterministic strategy trace
against that same snapshot.  It never creates a connection, persists a run,
calls an exchange write endpoint, or enables LIVE.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from datetime import datetime
from decimal import Decimal

from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot
from app.domain.deterministic_backtest_contracts import BacktestExecutionKind, BacktestRunFacts
from app.domain.gate_backtest_dataset_contracts import build_gate_backtest_dataset
from app.domain.paper_shadow_contracts import PaperShadowRunFacts
from app.domain.portfolio_risk_contracts import CooldownFact, PositionSizingRequest
from app.domain.strategy_library_contracts import StrategyDefinition
from app.services.deterministic_backtest_service import DeterministicBacktestService, DeterministicStrategyBacktest
from app.services.gate_testnet_market_session_service import GateTestnetMarketSessionReceipt, GateTestnetMarketSessionRequest, GateTestnetMarketSessionService
from app.services.research_pipeline import ResearchPipeline, ResearchPipelineResult


GATE_NON_LIVE_RESEARCH_VERSION = "gate-non-live-research-v1"


class GateNonLiveResearchError(ValueError):
    """The shared Gate snapshot cannot form a safe non-live run."""


def _typed_fact(value: object, expected: type, required: tuple[str, ...]) -> bool:
    """Accept canonical immutable facts and isolated-loader equivalents."""
    return isinstance(value, expected) or (
        type(value).__name__ == expected.__name__
        and all(hasattr(value, name) for name in required)
    )


@dataclass(frozen=True, slots=True)
class GateNonLiveResearchResult:
    session: GateTestnetMarketSessionReceipt
    dataset: BacktestDatasetSnapshot
    pipeline: ResearchPipelineResult
    deterministic_backtest: DeterministicStrategyBacktest
    result_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.session, GateTestnetMarketSessionReceipt) or not _typed_fact(
            self.dataset,
            BacktestDatasetSnapshot,
            ("dataset_snapshot_id", "dataset_fingerprint", "bars", "instrument_id"),
        ):
            raise GateNonLiveResearchError("session and dataset must be typed")
        if not _typed_fact(
            self.pipeline,
            ResearchPipelineResult,
            ("result_fingerprint", "to_public_dict"),
        ) or not _typed_fact(
            self.deterministic_backtest,
            DeterministicStrategyBacktest,
            ("dataset", "result_fingerprint", "to_public_dict"),
        ):
            raise GateNonLiveResearchError("pipeline and deterministic backtest must be typed")
        if self.dataset.dataset_snapshot_id != self.session.request.snapshot_id:
            raise GateNonLiveResearchError("dataset/session snapshot mismatch")
        if self.deterministic_backtest.dataset.dataset_snapshot_id != self.dataset.dataset_snapshot_id:
            raise GateNonLiveResearchError("deterministic backtest dataset mismatch")
        encoded = json.dumps({
            "version": GATE_NON_LIVE_RESEARCH_VERSION,
            "session": self.session.session_fingerprint,
            "dataset": self.dataset.dataset_fingerprint,
            "pipeline": self.pipeline.result_fingerprint,
            "deterministic_backtest": self.deterministic_backtest.result_fingerprint,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "result_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": GATE_NON_LIVE_RESEARCH_VERSION,
            "session_fingerprint": self.session.session_fingerprint,
            "dataset_fingerprint": self.dataset.dataset_fingerprint,
            "pipeline": self.pipeline.to_public_dict(),
            "deterministic_backtest": self.deterministic_backtest.to_public_dict(),
            "result_fingerprint": self.result_fingerprint,
            "live_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class GateNonLiveResearchOrchestrator:
    session_service: GateTestnetMarketSessionService
    pipeline: ResearchPipeline = ResearchPipeline()
    backtest_service: DeterministicBacktestService = DeterministicBacktestService()

    def run(
        self,
        session_request: GateTestnetMarketSessionRequest,
        strategy: StrategyDefinition,
        sizing_request: PositionSizingRequest,
        paper_shadow_run: PaperShadowRunFacts,
        backtest_run: BacktestRunFacts,
        *,
        signal_id: str,
        request_fingerprint: str,
        decided_at: datetime,
        order_quantity: Decimal,
        execution_kind: BacktestExecutionKind = BacktestExecutionKind.MARKET,
        cooldown: CooldownFact | None = None,
        now: datetime | None = None,
    ) -> GateNonLiveResearchResult:
        if not isinstance(self.session_service, GateTestnetMarketSessionService):
            raise GateNonLiveResearchError("session_service must be typed")
        if not isinstance(session_request, GateTestnetMarketSessionRequest):
            raise GateNonLiveResearchError("session_request must be typed")
        if not isinstance(strategy, StrategyDefinition) or not isinstance(sizing_request, PositionSizingRequest):
            raise GateNonLiveResearchError("strategy and sizing request must be typed")
        if not isinstance(paper_shadow_run, PaperShadowRunFacts) or not isinstance(backtest_run, BacktestRunFacts):
            raise GateNonLiveResearchError("run facts must be typed")
        if paper_shadow_run.dataset_snapshot_id != session_request.snapshot_id or backtest_run.dataset_snapshot_id != session_request.snapshot_id:
            raise GateNonLiveResearchError("run snapshot identities must match session")
        try:
            session = self.session_service.read(session_request)
            dataset = build_gate_backtest_dataset(
                session.evidence.candles,
                dataset_snapshot_id=session_request.snapshot_id,
                as_of=session_request.observed_at,
            )
            pipeline = self.pipeline.evaluate_dataset(
                strategy,
                dataset,
                sizing_request,
                paper_shadow_run,
                signal_id=signal_id,
                request_fingerprint=request_fingerprint,
                decided_at=decided_at,
                cooldown=cooldown,
                now=now,
            )
            deterministic = self.backtest_service.run(
                backtest_run,
                dataset,
                strategy,
                order_quantity=order_quantity,
                execution_kind=execution_kind,
            )
            return GateNonLiveResearchResult(session, dataset, pipeline, deterministic)
        except GateNonLiveResearchError:
            raise
        except Exception as exc:
            raise GateNonLiveResearchError("Gate non-live research run failed closed") from exc


__all__ = [
    "GATE_NON_LIVE_RESEARCH_VERSION",
    "GateNonLiveResearchError",
    "GateNonLiveResearchOrchestrator",
    "GateNonLiveResearchResult",
]
