"""Paper/Shadow orchestration over deterministic, typed domain facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.domain.paper_shadow_contracts import (
    PaperShadowDecision,
    PaperShadowRunFacts,
    SimulationDisposition,
    SimulationMode,
)
from app.domain.paper_shadow_reducer_contracts import (
    PaperShadowDecisionSet,
    PaperShadowReducerError,
    SimulationRecordDisposition,
    record_paper_shadow_decision,
)
from app.domain.portfolio_risk_contracts import (
    PositionSizingDecision,
    SizingDisposition,
)
from app.domain.strategy_library_contracts import StrategySignalFact


class PaperShadowServiceError(ValueError):
    """Invalid simulation orchestration input."""


@dataclass(frozen=True, slots=True)
class PaperShadowCandidate:
    """One typed strategy/risk result awaiting simulation recording."""

    signal: StrategySignalFact
    sizing: PositionSizingDecision
    request_fingerprint: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.signal, StrategySignalFact):
            raise PaperShadowServiceError("candidate signal must be typed")
        if not isinstance(self.sizing, PositionSizingDecision):
            raise PaperShadowServiceError("candidate sizing must be typed")
        if not isinstance(self.request_fingerprint, str) or not self.request_fingerprint.strip():
            raise PaperShadowServiceError("candidate request_fingerprint is required")


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

    def record_batch(
        self,
        run: PaperShadowRunFacts,
        candidates: tuple[PaperShadowCandidate, ...],
        *,
        existing: PaperShadowDecisionSet | None = None,
    ) -> "PaperShadowBatchResult":
        """Apply a deterministic batch with in-memory replay/conflict rules.

        ``existing`` represents caller-owned facts; it is never mutated.  A
        later repository can persist the returned decision set atomically.
        """

        if not isinstance(run, PaperShadowRunFacts):
            raise PaperShadowServiceError("run must be typed")
        if not isinstance(candidates, tuple) or any(not isinstance(item, PaperShadowCandidate) for item in candidates):
            raise PaperShadowServiceError("candidates must be an explicit typed tuple")
        current = existing or PaperShadowDecisionSet(run.run_id, run.mode)
        if current.run_id != run.run_id or current.mode is not run.mode:
            raise PaperShadowServiceError("existing decision set scope does not match run")
        dispositions = []
        decisions = []
        try:
            for candidate in candidates:
                decision = self.decide(
                    run,
                    candidate.signal,
                    candidate.sizing,
                    request_fingerprint=candidate.request_fingerprint,
                    decided_at=candidate.decided_at,
                )
                recorded = record_paper_shadow_decision(current, decision)
                dispositions.append(recorded.disposition)
                decisions.append(decision)
                if recorded.disposition is SimulationRecordDisposition.CONFLICT:
                    raise PaperShadowServiceError("paper/shadow replay conflict")
                current = recorded.decision_set
        except PaperShadowReducerError as exc:
            raise PaperShadowServiceError("paper/shadow decision set is invalid") from exc
        disposition = SimulationRecordDisposition.CREATED if SimulationRecordDisposition.CREATED in dispositions else SimulationRecordDisposition.REPLAYED
        return PaperShadowBatchResult(disposition, current, tuple(decisions))


@dataclass(frozen=True, slots=True)
class PaperShadowBatchResult:
    disposition: SimulationRecordDisposition
    decision_set: PaperShadowDecisionSet
    decisions: tuple[PaperShadowDecision, ...]
    batch_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, SimulationRecordDisposition):
            raise PaperShadowServiceError("batch disposition must be typed")
        if not isinstance(self.decision_set, PaperShadowDecisionSet):
            raise PaperShadowServiceError("batch decision_set must be typed")
        if not isinstance(self.decisions, tuple) or any(not isinstance(item, PaperShadowDecision) for item in self.decisions):
            raise PaperShadowServiceError("batch decisions must be typed")
        object.__setattr__(self, "batch_fingerprint", self.decision_set.replay_fingerprint)


__all__ = [
    "PaperShadowBatchResult",
    "PaperShadowCandidate",
    "PaperShadowService",
    "PaperShadowServiceError",
]
