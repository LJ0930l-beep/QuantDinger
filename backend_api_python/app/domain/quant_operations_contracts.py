"""Typed, read-only operating posture for the non-live research product.

The snapshot composes already validated research, rehearsal, and release
evidence.  It is deliberately a projection contract: it cannot enable live
trading, create a connection, or persist an operational decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any

from .gate_testnet_rehearsal_contracts import GateTestnetRehearsalResult, GateTestnetRehearsalStatus
from .production_readiness_contracts import (
    ProductionReadinessEvidence,
    ProductionReadinessStatus,
    derive_production_readiness,
)
from .research_readiness_contracts import ResearchReadinessStatus, ResearchReadinessView


QUANT_OPERATIONS_CONTRACT_VERSION = "quant-operations-v1"


class QuantOperationsError(ValueError):
    """Invalid or unsafe composed operational evidence."""


class QuantOperationsStatus(str, Enum):
    BLOCKED = "BLOCKED"
    TESTNET_READY = "TESTNET_READY"
    CANARY_READY = "CANARY_READY"
    PRODUCTION_READY = "PRODUCTION_READY"


@dataclass(frozen=True, slots=True)
class QuantOperationsSnapshot:
    """One deterministic, non-live view across all release gates."""

    research: ResearchReadinessView
    production_evidence: ProductionReadinessEvidence
    rehearsal: GateTestnetRehearsalResult
    status: QuantOperationsStatus
    reason_codes: tuple[str, ...]
    live_enabled: bool = False
    operations_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.research, ResearchReadinessView):
            raise QuantOperationsError("research must use ResearchReadinessView")
        if not isinstance(self.production_evidence, ProductionReadinessEvidence):
            raise QuantOperationsError("production_evidence must use typed evidence")
        if not isinstance(self.rehearsal, GateTestnetRehearsalResult):
            raise QuantOperationsError("rehearsal must use GateTestnetRehearsalResult")
        if not isinstance(self.status, QuantOperationsStatus):
            raise QuantOperationsError("status must be typed")
        if not isinstance(self.reason_codes, tuple) or any(not isinstance(item, str) or not item or item.strip() != item for item in self.reason_codes):
            raise QuantOperationsError("reason_codes must be canonical")
        if self.live_enabled:
            raise QuantOperationsError("operational snapshot cannot authorize live trading")
        if self.production_evidence.live_enabled or self.research.live_enabled:
            raise QuantOperationsError("composed evidence cannot contain live authorization")
        material = {
            "version": QUANT_OPERATIONS_CONTRACT_VERSION,
            "research": self.research.readiness_fingerprint,
            "production": self.production_evidence.readiness_fingerprint,
            "rehearsal": self.rehearsal.rehearsal_fingerprint,
            "status": self.status.value,
            "reason_codes": self.reason_codes,
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "operations_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "contract_version": QUANT_OPERATIONS_CONTRACT_VERSION,
            "status": self.status.value,
            "research_status": self.research.status.value,
            "rehearsal_status": self.rehearsal.status.value,
            "production_status": derive_production_readiness(self.production_evidence).value,
            "reason_codes": list(self.reason_codes),
            "operations_fingerprint": self.operations_fingerprint,
            "live_enabled": False,
        }


def derive_quant_operations(
    research: ResearchReadinessView,
    production_evidence: ProductionReadinessEvidence,
    rehearsal: GateTestnetRehearsalResult,
) -> QuantOperationsSnapshot:
    """Derive posture without allowing a caller to override any gate."""

    if not isinstance(research, ResearchReadinessView):
        raise QuantOperationsError("research must use ResearchReadinessView")
    if not isinstance(production_evidence, ProductionReadinessEvidence):
        raise QuantOperationsError("production_evidence must use typed evidence")
    if not isinstance(rehearsal, GateTestnetRehearsalResult):
        raise QuantOperationsError("rehearsal must use GateTestnetRehearsalResult")
    production_status = derive_production_readiness(production_evidence)
    reasons: list[str] = []
    if research.status is ResearchReadinessStatus.BLOCKED:
        reasons.append("research_blocked")
    elif research.status is ResearchReadinessStatus.DEGRADED:
        reasons.append("research_degraded")
    if rehearsal.status is not GateTestnetRehearsalStatus.READY:
        reasons.append(f"rehearsal_{rehearsal.status.value.lower()}")
    if production_status is ProductionReadinessStatus.BLOCKED:
        reasons.append("production_evidence_blocked")
    elif production_status is ProductionReadinessStatus.TESTNET_READY:
        reasons.append("recovery_or_schema_evidence_pending")
    elif production_status is ProductionReadinessStatus.CANARY_READY:
        reasons.append("rollback_or_operator_gate_pending")
    if reasons:
        status = QuantOperationsStatus.BLOCKED if any(code.endswith("blocked") or code.endswith("failed") for code in reasons) else QuantOperationsStatus.TESTNET_READY
        if production_status is ProductionReadinessStatus.CANARY_READY and status is not QuantOperationsStatus.BLOCKED:
            status = QuantOperationsStatus.CANARY_READY
    else:
        status = QuantOperationsStatus.PRODUCTION_READY
        reasons.append("all_non_live_release_gates_ready")
    return QuantOperationsSnapshot(research, production_evidence, rehearsal, status, tuple(reasons))


__all__ = [
    "QUANT_OPERATIONS_CONTRACT_VERSION",
    "QuantOperationsError",
    "QuantOperationsStatus",
    "QuantOperationsSnapshot",
    "derive_quant_operations",
]
