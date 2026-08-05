"""Pure research readiness tests; no Flask, database, network, or credentials."""

import importlib.util
import sys
import unittest
from enum import Enum
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load():
    names = {
        "app": None,
        "app.domain": None,
        "app.domain.backtest_result_contracts": None,
        "app.domain.paper_shadow_run_result_contracts": None,
        "app.domain.gate_testnet_readiness_contracts": ROOT / "app/domain/gate_testnet_readiness_contracts.py",
        "app.domain.research_readiness_contracts": ROOT / "app/domain/research_readiness_contracts.py",
    }
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain

        class BacktestResultStatus(str, Enum):
            READY = "READY"
            UNAVAILABLE = "UNAVAILABLE"
            UNAUTHORIZED = "UNAUTHORIZED"

        class PaperShadowRunStatus(str, Enum):
            RUNNING = "RUNNING"
            COMPLETED = "COMPLETED"
            FAILED = "FAILED"

        backtest = ModuleType("app.domain.backtest_result_contracts")
        backtest.BacktestResultStatus = BacktestResultStatus
        paper = ModuleType("app.domain.paper_shadow_run_result_contracts")
        paper.PaperShadowRunStatus = PaperShadowRunStatus
        sys.modules["app.domain.backtest_result_contracts"] = backtest
        sys.modules["app.domain.paper_shadow_run_result_contracts"] = paper
        for name in ("app.domain.gate_testnet_readiness_contracts", "app.domain.research_readiness_contracts"):
            spec = importlib.util.spec_from_file_location(name, names[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules["app.domain.research_readiness_contracts"], BacktestResultStatus, PaperShadowRunStatus
    finally:
        for name, original in names.items():
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


M, BacktestStatus, PaperStatus = load()
GateStatus = M.GateTestnetReadinessStatus


class ResearchReadinessContractTests(unittest.TestCase):
    def test_ready_requires_gate_backtest_and_paper_shadow_evidence(self):
        view = M.derive_research_readiness(GateStatus.READY, BacktestStatus.READY, PaperStatus.COMPLETED)
        self.assertEqual(view.status, M.ResearchReadinessStatus.READY)
        self.assertEqual(view.reason_codes, ("non_live_research_facts_ready",))
        self.assertFalse(view.live_enabled)

    def test_missing_or_failed_evidence_is_degraded(self):
        missing = M.derive_research_readiness(GateStatus.READY, BacktestStatus.UNAVAILABLE, None)
        self.assertEqual(missing.status, M.ResearchReadinessStatus.DEGRADED)
        self.assertIn("backtest_unavailable", missing.reason_codes)
        self.assertIn("paper_shadow_unavailable", missing.reason_codes)
        failed = M.derive_research_readiness(GateStatus.READY, BacktestStatus.READY, PaperStatus.FAILED)
        self.assertEqual(failed.status, M.ResearchReadinessStatus.DEGRADED)

    def test_blocked_gate_is_blocked(self):
        view = M.derive_research_readiness(GateStatus.BLOCKED, BacktestStatus.READY, PaperStatus.COMPLETED)
        self.assertEqual(view.status, M.ResearchReadinessStatus.BLOCKED)
        self.assertEqual(view.reason_codes, ("gate_testnet_blocked",))

    def test_live_enabled_and_untyped_values_fail_closed(self):
        with self.assertRaises(M.ResearchReadinessError):
            M.derive_research_readiness(GateStatus.READY, BacktestStatus.READY, PaperStatus.COMPLETED, live_enabled=True)
        with self.assertRaises(M.ResearchReadinessError):
            M.derive_research_readiness("READY", BacktestStatus.READY, PaperStatus.COMPLETED)

    def test_fingerprint_is_deterministic_and_public_surface_is_non_live(self):
        first = M.derive_research_readiness(GateStatus.READY, BacktestStatus.READY, PaperStatus.COMPLETED)
        second = M.derive_research_readiness(GateStatus.READY, BacktestStatus.READY, PaperStatus.COMPLETED)
        self.assertEqual(first.readiness_fingerprint, second.readiness_fingerprint)
        self.assertFalse(first.to_public_dict()["live_enabled"])


if __name__ == "__main__":
    unittest.main()
