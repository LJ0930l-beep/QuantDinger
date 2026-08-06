import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = (
        "app.domain.production_readiness_contracts",
        "app.domain.deployment_readiness_contracts",
        "app.services.deployment_readiness_file_provider",
    )
    old = {name: __import__("sys").modules.get(name) for name in names}
    try:
        for name, relative in (
            (names[0], "app/domain/production_readiness_contracts.py"),
            (names[1], "app/domain/deployment_readiness_contracts.py"),
            (names[2], "app/services/deployment_readiness_file_provider.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, ROOT / relative)
            module = importlib.util.module_from_spec(spec)
            __import__("sys").modules[name] = module
            spec.loader.exec_module(module)
        return tuple(__import__("sys").modules[name] for name in names)
    finally:
        for name, value in old.items():
            if value is None:
                __import__("sys").modules.pop(name, None)
            else:
                __import__("sys").modules[name] = value


Production, Deployment, Provider = _load()


def _profile(environment="TESTNET"):
    return {
        "release_id": "release-1",
        "environment": environment,
        "artifact_digest": "a" * 64,
        "schema_fingerprint": "b" * 64,
        "config_fingerprint": "c" * 64,
        "rollback_release_id": "release-0",
        "rollback_verified": True,
        "live_enabled": False,
    }


class DeploymentArtifactTests(unittest.TestCase):
    def _write(self, payload):
        fd, raw_path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        path = Path(raw_path)
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_loads_and_derives_testnet_status(self):
        profile = _profile()
        typed = Deployment.DeploymentReleaseProfile(
            release_id=profile["release_id"],
            environment=Deployment.DeploymentEnvironment.TESTNET,
            artifact_digest=profile["artifact_digest"],
            schema_fingerprint=profile["schema_fingerprint"],
            config_fingerprint=profile["config_fingerprint"],
            rollback_release_id=profile["rollback_release_id"],
            rollback_verified=True,
        )
        profile["deployment_fingerprint"] = typed.deployment_fingerprint
        path = self._write({"contract_version": Deployment.DEPLOYMENT_READINESS_CONTRACT_VERSION, "profile": profile, "production_status": "TESTNET_READY"})
        loaded, status = Provider.load_deployment_readiness_artifact(path)
        self.assertEqual(loaded.deployment_fingerprint, typed.deployment_fingerprint)
        self.assertEqual(status, Deployment.DeploymentReadinessStatus.TESTNET_READY)

    def test_relative_and_sensitive_artifacts_fail_closed(self):
        with self.assertRaises(Provider.DeploymentReadinessArtifactError):
            Provider.load_deployment_readiness_artifact(Path("relative.json"))
        profile = _profile()
        path = self._write({"contract_version": Deployment.DEPLOYMENT_READINESS_CONTRACT_VERSION, "profile": profile, "production_status": "TESTNET_READY", "api_secret": "synthetic"})
        with self.assertRaises(Provider.DeploymentReadinessArtifactError):
            Provider.load_deployment_readiness_artifact(path)

    def test_live_enabled_is_rejected(self):
        profile = _profile()
        profile["live_enabled"] = True
        path = self._write({"contract_version": Deployment.DEPLOYMENT_READINESS_CONTRACT_VERSION, "profile": profile, "production_status": "PRODUCTION_READY"})
        with self.assertRaises(Provider.DeploymentReadinessArtifactError):
            Provider.load_deployment_readiness_artifact(path)


if __name__ == "__main__":
    unittest.main()
