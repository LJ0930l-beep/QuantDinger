"""Immutable lifecycle and monitoring facts for PAPER/SHADOW runs.

This is a pure result boundary.  It does not persist a run, recover a worker,
or contact a venue.  A repository may later store/replay the same immutable
facts without changing the read-only API shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .paper_shadow_contracts import PaperShadowContractError, PaperShadowRunFacts, SimulationDisposition, simulation_fingerprint
from .paper_shadow_reducer_contracts import PaperShadowDecisionSet


PAPER_SHADOW_RESULT_CONTRACT_VERSION = "paper-shadow-result-v1"


class PaperShadowRunResultError(PaperShadowContractError):
    """Invalid lifecycle or cross-scope simulation result facts."""


class PaperShadowRunStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise PaperShadowRunResultError(f"{field_name} must use a zero UTC offset")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class PaperShadowRunResult:
    run: PaperShadowRunFacts
    decision_set: PaperShadowDecisionSet
    status: PaperShadowRunStatus
    completed_at: datetime | None = None
    failure_reason: str | None = None
    result_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, PaperShadowRunFacts) or not isinstance(self.decision_set, PaperShadowDecisionSet):
            raise PaperShadowRunResultError("run and decision_set must be typed")
        if self.run.run_id != self.decision_set.run_id or self.run.mode is not self.decision_set.mode:
            raise PaperShadowRunResultError("run and decision set scope mismatch")
        if not isinstance(self.status, PaperShadowRunStatus):
            raise PaperShadowRunResultError("status must use PaperShadowRunStatus")
        if self.completed_at is not None:
            completed = _utc(self.completed_at, "completed_at")
            if completed < self.run.started_at:
                raise PaperShadowRunResultError("completed_at cannot precede started_at")
            object.__setattr__(self, "completed_at", completed)
        if self.status is PaperShadowRunStatus.RUNNING:
            if self.completed_at is not None or self.failure_reason is not None:
                raise PaperShadowRunResultError("RUNNING result cannot carry terminal facts")
        elif self.completed_at is None:
            raise PaperShadowRunResultError("terminal result requires completed_at")
        if self.status is PaperShadowRunStatus.FAILED:
            if not isinstance(self.failure_reason, str) or not self.failure_reason or self.failure_reason.strip() != self.failure_reason:
                raise PaperShadowRunResultError("FAILED result requires canonical failure_reason")
        elif self.failure_reason is not None:
            raise PaperShadowRunResultError("only FAILED result may carry failure_reason")
        object.__setattr__(self, "result_fingerprint", self._fingerprint())

    @property
    def decision_count(self) -> int:
        return len(self.decision_set.decisions)

    @property
    def accepted_count(self) -> int:
        return sum(1 for item in self.decision_set.decisions if item.disposition is SimulationDisposition.ACCEPTED)

    @property
    def rejected_count(self) -> int:
        return sum(1 for item in self.decision_set.decisions if item.disposition is SimulationDisposition.REJECTED)

    def _fingerprint(self) -> str:
        return simulation_fingerprint({
            "contract_version": PAPER_SHADOW_RESULT_CONTRACT_VERSION,
            "run_id": self.run.run_id,
            "dataset_snapshot_id": self.run.dataset_snapshot_id,
            "mode": self.run.mode,
            "strategy_fingerprint": self.run.strategy_fingerprint,
            "risk_policy_fingerprint": self.run.risk_policy_fingerprint,
            "tolerance_policy_fingerprint": self.run.tolerance_policy_fingerprint,
            "started_at": self.run.started_at,
            "status": self.status,
            "completed_at": self.completed_at,
            "failure_reason": self.failure_reason,
            "decision_replay_fingerprint": self.decision_set.replay_fingerprint,
        })

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "contract_version": PAPER_SHADOW_RESULT_CONTRACT_VERSION,
            "run_id": self.run.run_id,
            "dataset_snapshot_id": self.run.dataset_snapshot_id,
            "mode": self.run.mode.value,
            "strategy_fingerprint": self.run.strategy_fingerprint,
            "risk_policy_fingerprint": self.run.risk_policy_fingerprint,
            "tolerance_policy_fingerprint": self.run.tolerance_policy_fingerprint,
            "started_at": self.run.started_at.isoformat(),
            "status": self.status.value,
            "completed_at": None if self.completed_at is None else self.completed_at.isoformat(),
            "failure_reason": self.failure_reason,
            "decision_count": self.decision_count,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "decision_replay_fingerprint": self.decision_set.replay_fingerprint,
            "result_fingerprint": self.result_fingerprint,
        }


__all__ = [
    "PAPER_SHADOW_RESULT_CONTRACT_VERSION",
    "PaperShadowRunResult",
    "PaperShadowRunResultError",
    "PaperShadowRunStatus",
]
