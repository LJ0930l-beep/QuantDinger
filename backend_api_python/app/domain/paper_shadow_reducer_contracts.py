"""Pure replay/conflict reducer for PAPER/SHADOW decisions.

The reducer is an in-memory, deterministic contract.  It does not persist,
commit, submit orders, or access an exchange.  A later repository may use the
same immutable comparison rules at its transaction boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from app.domain.paper_shadow_contracts import (
    PaperShadowContractError,
    PaperShadowDecision,
    SimulationDisposition,
    SimulationMode,
    simulation_fingerprint,
)


PAPER_SHADOW_REDUCER_CONTRACT_VERSION = "paper-shadow-reducer-v1"


class PaperShadowReducerError(PaperShadowContractError):
    """The supplied decision set violates replay or scope invariants."""


class SimulationRecordDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class PaperShadowDecisionSet:
    run_id: str
    mode: SimulationMode
    decisions: Tuple[PaperShadowDecision, ...] = ()
    replay_fingerprint: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.mode, SimulationMode) or self.mode is SimulationMode.DISABLED:
            raise PaperShadowReducerError("decision set requires PAPER or SHADOW")
        if not isinstance(self.run_id, str) or not self.run_id or self.run_id.strip() != self.run_id:
            raise PaperShadowReducerError("run_id must be canonical text")
        if not isinstance(self.decisions, tuple) or any(not isinstance(item, PaperShadowDecision) for item in self.decisions):
            raise PaperShadowReducerError("decisions must be an explicit typed tuple")
        if any(item.run_id != self.run_id or item.mode is not self.mode for item in self.decisions):
            raise PaperShadowReducerError("decision scope does not match the set")
        keys = [item.request_fingerprint for item in self.decisions]
        if len(keys) != len(set(keys)):
            raise PaperShadowReducerError("request fingerprint must be unique")
        expected = simulation_fingerprint(self.decisions)
        if self.replay_fingerprint and self.replay_fingerprint != expected:
            raise PaperShadowReducerError("replay_fingerprint does not match decisions")
        object.__setattr__(self, "replay_fingerprint", expected)


@dataclass(frozen=True, slots=True)
class PaperShadowRecordResult:
    disposition: SimulationRecordDisposition
    decision_set: PaperShadowDecisionSet


def _immutable_facts(value: PaperShadowDecision) -> tuple[object, ...]:
    return (
        value.run_id, value.request_fingerprint, value.economic_fingerprint,
        value.mode, value.disposition, value.quantity, value.notional,
        value.reason, value.decided_at,
    )


def record_paper_shadow_decision(
    decision_set: PaperShadowDecisionSet,
    decision: PaperShadowDecision,
) -> PaperShadowRecordResult:
    """Append one decision or return deterministic replay/conflict."""

    if not isinstance(decision_set, PaperShadowDecisionSet) or not isinstance(decision, PaperShadowDecision):
        raise PaperShadowReducerError("typed decision set and decision are required")
    if decision.run_id != decision_set.run_id or decision.mode is not decision_set.mode:
        raise PaperShadowReducerError("decision scope mismatch")
    existing = next((item for item in decision_set.decisions if item.request_fingerprint == decision.request_fingerprint), None)
    if existing is not None:
        if _immutable_facts(existing) == _immutable_facts(decision):
            return PaperShadowRecordResult(SimulationRecordDisposition.REPLAYED, decision_set)
        return PaperShadowRecordResult(SimulationRecordDisposition.CONFLICT, decision_set)
    updated = PaperShadowDecisionSet(decision_set.run_id, decision_set.mode, decision_set.decisions + (decision,))
    return PaperShadowRecordResult(SimulationRecordDisposition.CREATED, updated)


__all__ = [
    "PAPER_SHADOW_REDUCER_CONTRACT_VERSION",
    "PaperShadowDecisionSet",
    "PaperShadowRecordResult",
    "PaperShadowReducerError",
    "SimulationRecordDisposition",
    "record_paper_shadow_decision",
]
