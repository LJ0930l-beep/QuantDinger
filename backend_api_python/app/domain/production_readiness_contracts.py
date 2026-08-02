"""Fail-closed release evidence for testnet, canary, and production gates.

This contract records readiness only.  It cannot enable live trading and does
not replace the separate execution safety kernel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json


PRODUCTION_READINESS_CONTRACT_VERSION = "production-readiness-v1"


class ProductionReadinessError(ValueError):
    """Invalid or unsafe release evidence."""


class ProductionReadinessStatus(str, Enum):
    BLOCKED = "BLOCKED"
    TESTNET_READY = "TESTNET_READY"
    CANARY_READY = "CANARY_READY"
    PRODUCTION_READY = "PRODUCTION_READY"


@dataclass(frozen=True, slots=True)
class ProductionReadinessEvidence:
    backend_ci_passed: bool
    security_ci_passed: bool
    architecture_guard_passed: bool
    schema_parity_passed: bool
    recovery_tests_passed: bool
    testnet_read_passed: bool
    rollback_plan_verified: bool
    operator_approval: bool
    live_enabled: bool = False
    readiness_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        flags = (
            self.backend_ci_passed, self.security_ci_passed, self.architecture_guard_passed,
            self.schema_parity_passed, self.recovery_tests_passed, self.testnet_read_passed,
            self.rollback_plan_verified, self.operator_approval, self.live_enabled,
        )
        if any(not isinstance(value, bool) for value in flags):
            raise ProductionReadinessError("readiness evidence flags must be boolean")
        if self.live_enabled:
            raise ProductionReadinessError("readiness evidence cannot authorize live trading")
        material = {
            "version": PRODUCTION_READINESS_CONTRACT_VERSION,
            "backend_ci_passed": self.backend_ci_passed,
            "security_ci_passed": self.security_ci_passed,
            "architecture_guard_passed": self.architecture_guard_passed,
            "schema_parity_passed": self.schema_parity_passed,
            "recovery_tests_passed": self.recovery_tests_passed,
            "testnet_read_passed": self.testnet_read_passed,
            "rollback_plan_verified": self.rollback_plan_verified,
            "operator_approval": self.operator_approval,
            "live_enabled": False,
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        object.__setattr__(self, "readiness_fingerprint", hashlib.sha256(encoded.encode("ascii")).hexdigest())


def derive_production_readiness(evidence: ProductionReadinessEvidence) -> ProductionReadinessStatus:
    if not isinstance(evidence, ProductionReadinessEvidence):
        raise ProductionReadinessError("typed readiness evidence is required")
    if not evidence.testnet_read_passed:
        return ProductionReadinessStatus.BLOCKED
    if not (evidence.backend_ci_passed and evidence.security_ci_passed and evidence.architecture_guard_passed):
        return ProductionReadinessStatus.BLOCKED
    if not evidence.recovery_tests_passed or not evidence.schema_parity_passed:
        return ProductionReadinessStatus.TESTNET_READY
    if not evidence.rollback_plan_verified:
        return ProductionReadinessStatus.CANARY_READY
    if not evidence.operator_approval:
        return ProductionReadinessStatus.CANARY_READY
    return ProductionReadinessStatus.PRODUCTION_READY


__all__ = [
    "PRODUCTION_READINESS_CONTRACT_VERSION",
    "ProductionReadinessError",
    "ProductionReadinessEvidence",
    "ProductionReadinessStatus",
    "derive_production_readiness",
]
