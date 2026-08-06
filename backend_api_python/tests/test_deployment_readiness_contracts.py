"""Deployment gate tests; no deploy, network, database, or credentials."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = {
        "app": None,
        "app.domain": None,
        "app.domain.production_readiness_contracts": ROOT / "app/domain/production_readiness_contracts.py",
        "app.domain.deployment_readiness_contracts": ROOT / "app/domain/deployment_readiness_contracts.py",
        "app.services": None,
        "app.services.deployment_readiness_service": ROOT / "app/services/deployment_readiness_service.py",
    }
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        for name in ("app.domain.production_readiness_contracts", "app.domain.deployment_readiness_contracts", "app.services.deployment_readiness_service"):
            spec = importlib.util.spec_from_file_location(name, names[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules["app.domain.deployment_readiness_contracts"], sys.modules["app.domain.production_readiness_contracts"], sys.modules["app.services.deployment_readiness_service"]
    finally:
        for name, original in old.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


M, Production, Service = _load()


def _profile(environment):
    return M.DeploymentReleaseProfile(
        "release-1", environment, "a" * 64, "b" * 64, "c" * 64, "release-0", True,
    )


class DeploymentReadinessTests(unittest.TestCase):
    def test_testnet_profile_is_ready_without_live_authority(self):
        result = M.derive_deployment_readiness(_profile(M.DeploymentEnvironment.TESTNET), Production.ProductionReadinessStatus.TESTNET_READY)
        self.assertEqual(result, M.DeploymentReadinessStatus.TESTNET_READY)
        self.assertFalse(_profile(M.DeploymentEnvironment.TESTNET).to_public_dict()["live_enabled"])

    def test_canary_requires_verified_rollback(self):
        blocked_profile = M.DeploymentReleaseProfile("release-1", M.DeploymentEnvironment.CANARY, "a" * 64, "b" * 64, "c" * 64, "release-0", False)
        self.assertEqual(M.derive_deployment_readiness(blocked_profile, Production.ProductionReadinessStatus.PRODUCTION_READY), M.DeploymentReadinessStatus.BLOCKED)
        self.assertEqual(M.derive_deployment_readiness(_profile(M.DeploymentEnvironment.CANARY), Production.ProductionReadinessStatus.CANARY_READY), M.DeploymentReadinessStatus.CANARY_READY)

    def test_production_requires_production_evidence(self):
        self.assertEqual(M.derive_deployment_readiness(_profile(M.DeploymentEnvironment.PRODUCTION), Production.ProductionReadinessStatus.CANARY_READY), M.DeploymentReadinessStatus.CANARY_READY)
        self.assertEqual(M.derive_deployment_readiness(_profile(M.DeploymentEnvironment.PRODUCTION), Production.ProductionReadinessStatus.PRODUCTION_READY), M.DeploymentReadinessStatus.PRODUCTION_READY)

    def test_service_rejects_untyped_provider(self):
        with self.assertRaises(Service.DeploymentReadinessServiceError):
            Service.DeploymentReadinessService(lambda: ({}, "PRODUCTION_READY")).read_response()
        self.assertEqual(Service.DeploymentReadinessService().read_response()[0], 503)


if __name__ == "__main__":
    unittest.main()
