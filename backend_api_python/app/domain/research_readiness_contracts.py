"""Gate-first readiness facts for the non-live research product.

This contract is intentionally stricter than a UI health badge: it derives a
single status from typed Gate, backtest, and Paper/Shadow evidence and refuses
to represent a live-enabled state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any

from .backtest_result_contracts import BacktestResultStatus
from .gate_testnet_readiness_contracts import GateTestnetReadinessStatus
from .paper_shadow_run_result_contracts import PaperShadowRunStatus


RESEARCH_READINESS_CONTRACT_VERSION = "research-readiness-v1"


class ResearchReadinessError(ValueError):
    """Invalid or unsafe readiness evidence."""


class ResearchReadinessStatus(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class ResearchReadinessView:
    status: ResearchReadinessStatus
    gate: GateTestnetReadinessStatus
    backtest: BacktestResultStatus
    paper_shadow: PaperShadowRunStatus | None
    live_enabled: bool
    reason_codes: tuple[str, ...]
    readiness_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, ResearchReadinessStatus):
            raise ResearchReadinessError("status must be typed")
        if not isinstance(self.gate, GateTestnetReadinessStatus) or not isinstance(self.backtest, BacktestResultStatus):
            raise ResearchReadinessError("gate and backtest statuses must be typed")
        if self.paper_shadow is not None and not isinstance(self.paper_shadow, PaperShadowRunStatus):
            raise ResearchReadinessError("paper_shadow status must be typed")
        if not isinstance(self.live_enabled, bool) or self.live_enabled:
            raise ResearchReadinessError("research readiness cannot authorize live trading")
        if not isinstance(self.reason_codes, tuple) or any(not isinstance(item, str) or not item or item.strip() != item for item in self.reason_codes):
            raise ResearchReadinessError("reason_codes must be canonical")
        material = {
            "version": RESEARCH_READINESS_CONTRACT_VERSION,
            "status": self.status.value,
            "gate": self.gate.value,
            "backtest": self.backtest.value,
            "paper_shadow": None if self.paper_shadow is None else self.paper_shadow.value,
            "live_enabled": self.live_enabled,
            "reason_codes": self.reason_codes,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "readiness_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "contract_version": RESEARCH_READINESS_CONTRACT_VERSION,
            "status": self.status.value,
            "gate": self.gate.value,
            "backtest": self.backtest.value,
            "paper_shadow": None if self.paper_shadow is None else self.paper_shadow.value,
            "live_enabled": False,
            "reason_codes": list(self.reason_codes),
            "readiness_fingerprint": self.readiness_fingerprint,
        }


def derive_research_readiness(
    gate: GateTestnetReadinessStatus,
    backtest: BacktestResultStatus,
    paper_shadow: PaperShadowRunStatus | None,
    *,
    live_enabled: bool = False,
) -> ResearchReadinessView:
    if not isinstance(gate, GateTestnetReadinessStatus) or not isinstance(backtest, BacktestResultStatus):
        raise ResearchReadinessError("gate and backtest statuses must be typed")
    if paper_shadow is not None and not isinstance(paper_shadow, PaperShadowRunStatus):
        raise ResearchReadinessError("paper_shadow status must be typed")
    if live_enabled:
        raise ResearchReadinessError("live trading is outside research readiness")
    reasons: list[str] = []
    if gate is GateTestnetReadinessStatus.BLOCKED:
        reasons.append("gate_testnet_blocked")
    if backtest is BacktestResultStatus.UNAVAILABLE:
        reasons.append("backtest_unavailable")
    if backtest is BacktestResultStatus.UNAUTHORIZED:
        reasons.append("backtest_unauthorized")
    if paper_shadow is None:
        reasons.append("paper_shadow_unavailable")
    elif paper_shadow is PaperShadowRunStatus.FAILED:
        reasons.append("paper_shadow_failed")
    if gate is GateTestnetReadinessStatus.BLOCKED:
        status = ResearchReadinessStatus.BLOCKED
    elif reasons:
        status = ResearchReadinessStatus.DEGRADED
    else:
        status = ResearchReadinessStatus.READY
    if not reasons:
        reasons.append("non_live_research_facts_ready")
    return ResearchReadinessView(status, gate, backtest, paper_shadow, False, tuple(reasons))


__all__ = [
    "RESEARCH_READINESS_CONTRACT_VERSION",
    "ResearchReadinessError",
    "ResearchReadinessStatus",
    "ResearchReadinessView",
    "derive_research_readiness",
]
