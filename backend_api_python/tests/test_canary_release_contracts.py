from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
sys.modules.setdefault("app", app)
sys.modules.setdefault("app.domain", domain)

from app.domain.canary_release_contracts import (
    CanaryContractError,
    CanaryDecision,
    CanaryReleaseEvidence,
    evaluate_canary_promotion,
)
from app.domain.deployment_readiness_contracts import (
    DeploymentEnvironment,
    DeploymentReleaseProfile,
    derive_deployment_readiness_with_canary,
)
from app.domain.production_readiness_contracts import ProductionReadinessStatus


class CanaryReleaseContractTests(unittest.TestCase):
    def _evidence(self, **changes):
        values = dict(
            release_id="release-1", artifact_digest="a" * 64,
            sample_count=100, error_count=0, shadow_match_rate=Decimal("1"),
            reconciliation_healthy=True, kill_switch_clear=True,
            rollback_verified=True, observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        values.update(changes)
        return CanaryReleaseEvidence(**values)

    def test_healthy_evidence_is_only_a_promotion_candidate(self):
        result = evaluate_canary_promotion(self._evidence())
        self.assertEqual(result.decision, CanaryDecision.PROMOTION_CANDIDATE)
        self.assertFalse(result.live_enabled)

    def test_unhealthy_evidence_is_blocked_with_typed_reasons(self):
        result = evaluate_canary_promotion(self._evidence(sample_count=10, error_count=1, kill_switch_clear=False))
        self.assertEqual(result.decision, CanaryDecision.BLOCKED)
        self.assertIn("insufficient_samples", result.reasons)
        self.assertIn("kill_switch_not_clear", result.reasons)

    def test_live_and_non_utc_are_rejected(self):
        with self.assertRaises(CanaryContractError):
            self._evidence(live_enabled=True)
        with self.assertRaises(CanaryContractError):
            self._evidence(observed_at=datetime(2026, 1, 1))

    def test_canary_deployment_requires_promotion_candidate(self):
        profile = DeploymentReleaseProfile(
            "release-1", DeploymentEnvironment.CANARY, "a" * 64, "b" * 64, "c" * 64,
            "rollback-1", True,
        )
        blocked = evaluate_canary_promotion(self._evidence(sample_count=1))
        self.assertEqual(
            derive_deployment_readiness_with_canary(profile, ProductionReadinessStatus.CANARY_READY, blocked).value,
            "BLOCKED",
        )


if __name__ == "__main__":
    unittest.main()
