"""Pure non-live run manifest tests; no Flask, database, or network."""

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = {
        "app": None,
        "app.domain": None,
        "app.domain.decimal_values": ROOT / "app/domain/decimal_values.py",
        "app.domain.gate_readonly_contracts": ROOT / "app/domain/gate_readonly_contracts.py",
        "app.domain.paper_shadow_contracts": ROOT / "app/domain/paper_shadow_contracts.py",
        "app.domain.non_live_run_manifest_contracts": ROOT / "app/domain/non_live_run_manifest_contracts.py",
    }
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name in (
            "app.domain.decimal_values",
            "app.domain.gate_readonly_contracts",
            "app.domain.paper_shadow_contracts",
            "app.domain.non_live_run_manifest_contracts",
        ):
            spec = importlib.util.spec_from_file_location(name, names[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules["app.domain.non_live_run_manifest_contracts"], sys.modules["app.domain.gate_readonly_contracts"], sys.modules["app.domain.paper_shadow_contracts"]
    finally:
        for name, original in old.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


M, Gate, Paper = _load()


class NonLiveRunManifestTests(unittest.TestCase):
    def _manifest(self):
        return M.NonLiveRunManifest(
            "run-1", Gate.GateEnvironment.TESTNET, Paper.SimulationMode.SHADOW,
            M.NonLiveRunStatus.COMPLETED,
            M.input_fingerprint({"request": "request-1", "strategy": "smc-v1"}),
            "a" * 64, "b" * 64,
            datetime(2026, 1, 1, tzinfo=timezone.utc), "fixture_complete",
        )

    def test_completed_manifest_is_deterministic_and_non_live(self):
        first = self._manifest(); second = self._manifest()
        self.assertEqual(first.manifest_fingerprint, second.manifest_fingerprint)
        self.assertFalse(first.to_public_dict()["network_access"])
        self.assertFalse(first.to_public_dict()["live_enabled"])

    def test_blocked_manifest_requires_reason_and_no_fake_outputs(self):
        result = M.NonLiveRunManifest(
            "run-blocked", Gate.GateEnvironment.TESTNET, Paper.SimulationMode.PAPER,
            M.NonLiveRunStatus.BLOCKED,
            "c" * 64, None, None, datetime(2026, 1, 1, tzinfo=timezone.utc), "missing_testnet_read",
        )
        self.assertEqual(result.status, M.NonLiveRunStatus.BLOCKED)
        with self.assertRaises(M.NonLiveRunManifestError):
            M.NonLiveRunManifest(
                "run-live", Gate.GateEnvironment.TESTNET, Paper.SimulationMode.PAPER,
                M.NonLiveRunStatus.BLOCKED, "c" * 64, None, None,
                datetime(2026, 1, 1, tzinfo=timezone.utc), "blocked", True,
            )

    def test_live_and_non_testnet_inputs_fail_closed(self):
        with self.assertRaises(M.NonLiveRunManifestError):
            M.NonLiveRunManifest(
                "run-live", Gate.GateEnvironment.TESTNET, Paper.SimulationMode.PAPER,
                M.NonLiveRunStatus.BLOCKED, "c" * 64, None, None,
                datetime(2026, 1, 1, tzinfo=timezone.utc), "blocked", False, True,
            )
        with self.assertRaises(M.NonLiveRunManifestError):
            M.NonLiveRunManifest(
                "run-paper", Gate.GateMarketType.SPOT, Paper.SimulationMode.PAPER,
                M.NonLiveRunStatus.BLOCKED, "c" * 64, None, None,
                datetime(2026, 1, 1, tzinfo=timezone.utc), "blocked",
            )


if __name__ == "__main__":
    unittest.main()
