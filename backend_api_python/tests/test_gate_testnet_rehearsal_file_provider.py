import json
import importlib.util
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load_subject():
    names = (
        "app", "app.domain", "app.services",
        "app.domain.gate_testnet_rehearsal_contracts",
        "app.services.gate_testnet_rehearsal_file_provider",
    )
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        for name, relative in (
            (names[3], "app/domain/gate_testnet_rehearsal_contracts.py"),
            (names[4], "app/services/gate_testnet_rehearsal_file_provider.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, ROOT / relative)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[names[3]], sys.modules[names[4]]
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


CONTRACTS, PROVIDER = _load_subject()


class GateTestnetRehearsalFileProviderTests(unittest.TestCase):
    observed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _write(self, payload):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False)
        with handle:
            json.dump(payload, handle)
        return Path(handle.name)

    def _payload(self):
        result = CONTRACTS.GateTestnetRehearsalResult(
            CONTRACTS.GateTestnetRehearsalStatus.READY,
            (CONTRACTS.GateTestnetRehearsalSnapshot("snap-1", "session-1", "BTC_USDT", self.observed_at, "dataset-1"),),
            "public_read_fixture",
        )
        return result.to_public_dict()

    def test_loads_fingerprint_verified_artifact(self):
        path = self._write(self._payload())
        try:
            result = PROVIDER.load_gate_testnet_rehearsal_artifact(path)
            self.assertEqual(result.status, CONTRACTS.GateTestnetRehearsalStatus.READY)
            self.assertEqual(result.rehearsal_fingerprint, self._payload()["rehearsal_fingerprint"])
        finally:
            path.unlink(missing_ok=True)

    def test_tampering_and_sensitive_fields_fail_closed(self):
        payload = self._payload()
        payload["reason"] = "changed"
        path = self._write(payload)
        try:
            with self.assertRaises(PROVIDER.GateTestnetRehearsalArtifactError):
                PROVIDER.load_gate_testnet_rehearsal_artifact(path)
        finally:
            path.unlink(missing_ok=True)

        payload = self._payload()
        payload["api_key"] = "fixture-only"
        path = self._write(payload)
        try:
            with self.assertRaises(PROVIDER.GateTestnetRehearsalArtifactError):
                PROVIDER.load_gate_testnet_rehearsal_artifact(path)
        finally:
            path.unlink(missing_ok=True)

    def test_relative_paths_are_rejected(self):
        with self.assertRaises(PROVIDER.GateTestnetRehearsalArtifactError):
            PROVIDER.load_gate_testnet_rehearsal_artifact("rehearsal.json")


if __name__ == "__main__":
    unittest.main()
