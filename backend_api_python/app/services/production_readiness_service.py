"""Read-only release gate surface; it cannot activate any deployment mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from app.domain.production_readiness_contracts import ProductionReadinessEvidence, derive_production_readiness


class ProductionReadinessServiceError(RuntimeError):
    """Release evidence is unavailable or invalid."""


EvidenceProvider = Callable[[], object]


@dataclass(frozen=True, slots=True)
class ProductionReadinessService:
    provider: Optional[EvidenceProvider] = None

    def __post_init__(self) -> None:
        if self.provider is not None and not callable(self.provider):
            raise ProductionReadinessServiceError("evidence provider must be callable")

    def read_response(self, *, authorized: bool = True) -> tuple[int, dict]:
        if not isinstance(authorized, bool):
            raise ProductionReadinessServiceError("authorized must be boolean")
        if not authorized:
            return 401, {"status": "UNAVAILABLE", "live_enabled": False}
        if self.provider is None:
            return 503, {"status": "UNAVAILABLE", "live_enabled": False}
        try:
            evidence = self.provider()
            if not isinstance(evidence, ProductionReadinessEvidence):
                raise ProductionReadinessServiceError("provider returned invalid release evidence")
            status = derive_production_readiness(evidence)
            return 200, {
                "contract_version": "production-readiness-v1",
                "status": status.value,
                "readiness_fingerprint": evidence.readiness_fingerprint,
                "testnet_read_passed": evidence.testnet_read_passed,
                "testnet_execution_passed": evidence.testnet_execution_passed,
                "paper_recovery_passed": evidence.paper_recovery_passed,
                "rollback_plan_verified": evidence.rollback_plan_verified,
                "operator_approval": evidence.operator_approval,
                "live_enabled": False,
            }
        except ProductionReadinessServiceError:
            raise
        except Exception as exc:
            raise ProductionReadinessServiceError("release evidence provider failed") from exc


def service_from_app(app) -> ProductionReadinessService:
    return ProductionReadinessService(app.extensions.get("readonly_production_readiness_provider"))


__all__ = ["ProductionReadinessService", "ProductionReadinessServiceError", "service_from_app"]
