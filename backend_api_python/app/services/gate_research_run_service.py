"""End-to-end Gate read -> backtest -> strategy -> risk -> Paper/Shadow flow.

The service accepts only caller-owned, already typed facts and a read-only
Gate session.  It never creates a client, loads credentials, persists facts,
submits orders, or enables live trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from datetime import datetime
from typing import Optional

from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot
from app.domain.paper_shadow_contracts import PaperShadowRunFacts
from app.domain.portfolio_risk_contracts import CooldownFact, PositionSizingRequest
from app.domain.strategy_library_contracts import StrategyDefinition
from app.services.gate_testnet_market_session_service import GateTestnetMarketSessionReceipt, GateTestnetMarketSessionRequest, GateTestnetMarketSessionService
from app.services.research_pipeline import ResearchPipeline, ResearchPipelineResult
from app.domain.gate_backtest_dataset_contracts import build_gate_backtest_dataset


GATE_RESEARCH_RUN_CONTRACT_VERSION = "gate-research-run-v1"


class GateResearchRunServiceError(ValueError):
    """The caller-owned Gate research inputs cannot form a safe run."""


@dataclass(frozen=True, slots=True)
class GateResearchRunResult:
    session: GateTestnetMarketSessionReceipt
    dataset: BacktestDatasetSnapshot
    pipeline: ResearchPipelineResult
    run_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.session, GateTestnetMarketSessionReceipt) or not isinstance(self.dataset, BacktestDatasetSnapshot) or not isinstance(self.pipeline, ResearchPipelineResult):
            raise GateResearchRunServiceError("Gate research result facts must be typed")
        if self.dataset.dataset_snapshot_id != self.session.request.snapshot_id:
            raise GateResearchRunServiceError("dataset/session snapshot mismatch")
        encoded = json.dumps({
            "version": GATE_RESEARCH_RUN_CONTRACT_VERSION,
            "session": self.session.session_fingerprint,
            "dataset": self.dataset.dataset_fingerprint,
            "pipeline": self.pipeline.result_fingerprint,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "run_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, object]:
        return {
            "contract_version": GATE_RESEARCH_RUN_CONTRACT_VERSION,
            "run_fingerprint": self.run_fingerprint,
            "session_fingerprint": self.session.session_fingerprint,
            "dataset_fingerprint": self.dataset.dataset_fingerprint,
            "pipeline": self.pipeline.to_public_dict(),
            "live_enabled": False,
        }


@dataclass(frozen=True, slots=True)
class GateResearchRunService:
    session_service: GateTestnetMarketSessionService
    pipeline: ResearchPipeline = ResearchPipeline()

    def __post_init__(self) -> None:
        if not isinstance(self.session_service, GateTestnetMarketSessionService) or not isinstance(self.pipeline, ResearchPipeline):
            raise GateResearchRunServiceError("typed Gate session and research pipeline are required")

    def execute(
        self,
        session_request: GateTestnetMarketSessionRequest,
        strategy: StrategyDefinition,
        sizing_request: PositionSizingRequest,
        run: PaperShadowRunFacts,
        *,
        signal_id: str,
        request_fingerprint: str,
        decided_at: datetime,
        cooldown: CooldownFact | None = None,
        now: datetime | None = None,
    ) -> GateResearchRunResult:
        if not isinstance(session_request, GateTestnetMarketSessionRequest):
            raise GateResearchRunServiceError("session_request must be typed")
        if not isinstance(strategy, StrategyDefinition) or not isinstance(sizing_request, PositionSizingRequest) or not isinstance(run, PaperShadowRunFacts):
            raise GateResearchRunServiceError("strategy, sizing request, and run must be typed")
        try:
            session = self.session_service.read(session_request)
            dataset = build_gate_backtest_dataset(
                session.evidence.candles,
                dataset_snapshot_id=session_request.snapshot_id,
                as_of=session_request.observed_at,
            )
            pipeline_result = self.pipeline.evaluate_dataset(
                strategy,
                dataset,
                sizing_request,
                run,
                signal_id=signal_id,
                request_fingerprint=request_fingerprint,
                decided_at=decided_at,
                cooldown=cooldown,
                now=now,
            )
            return GateResearchRunResult(session, dataset, pipeline_result)
        except GateResearchRunServiceError:
            raise
        except Exception as exc:
            raise GateResearchRunServiceError("Gate research run failed closed") from exc


__all__ = ["GATE_RESEARCH_RUN_CONTRACT_VERSION", "GateResearchRunResult", "GateResearchRunService", "GateResearchRunServiceError"]
