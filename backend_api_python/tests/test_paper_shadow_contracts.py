import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    name = "app.domain.paper_shadow_contracts"
    names = ["app", "app.domain", name]
    old = {item: sys.modules.get(item) for item in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domain" / "paper_shadow_contracts.py")
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
    finally:
        for item in reversed(names):
            if old[item] is None: sys.modules.pop(item, None)
            else: sys.modules[item] = old[item]


M = load()


class PaperShadowTests(unittest.TestCase):
    def run_facts(self, mode=M.SimulationMode.PAPER):
        return M.PaperShadowRunFacts("run-1", mode, "dataset-1", "strategy-1", "risk-1", "tol-1", UTC)

    def test_only_paper_and_shadow_runs_are_allowed(self):
        self.assertEqual(self.run_facts().mode, M.SimulationMode.PAPER)
        self.assertEqual(self.run_facts(M.SimulationMode.SHADOW).mode, M.SimulationMode.SHADOW)
        with self.assertRaises(M.PaperShadowContractError): self.run_facts(M.SimulationMode.DISABLED)

    def test_run_facts_are_immutable_and_end_after_start(self):
        run = self.run_facts(); self.assertEqual(M.simulation_fingerprint(run), M.simulation_fingerprint(self.run_facts()))
        with self.assertRaises((AttributeError, TypeError)): run.mode = M.SimulationMode.SHADOW
        with self.assertRaises(M.PaperShadowContractError): M.PaperShadowRunFacts("run", M.SimulationMode.PAPER, "d", "s", "r", "t", UTC, UTC - timedelta(seconds=1))

    def test_cost_policy_fingerprint_is_optional_but_strict_when_present(self):
        run = M.PaperShadowRunFacts("run", M.SimulationMode.PAPER, "d", "s", "r", "t", UTC, cost_policy_fingerprint="a" * 64)
        self.assertEqual(run.cost_policy_fingerprint, "a" * 64)
        with self.assertRaises(M.PaperShadowContractError):
            M.PaperShadowRunFacts("run", M.SimulationMode.PAPER, "d", "s", "r", "t", UTC, cost_policy_fingerprint="not-a-sha")

    def test_decision_preserves_economic_and_request_identity(self):
        decision = M.PaperShadowDecision("run-1", "request-1", "economic-1", M.SimulationMode.SHADOW, M.SimulationDisposition.ACCEPTED, Decimal("0.1"), Decimal("10"), "mock accepted", UTC)
        self.assertEqual(decision.notional, Decimal("10")); self.assertEqual(decision.mode, M.SimulationMode.SHADOW)
        with self.assertRaises(M.PaperShadowContractError): M.PaperShadowDecision("run", "r", "e", M.SimulationMode.PAPER, M.SimulationDisposition.ACCEPTED, 0.1, Decimal("1"), "reason", UTC)

    def test_no_live_enum_or_disposition_can_be_constructed(self):
        self.assertNotIn("LIVE", [item.value for item in M.SimulationMode])
        with self.assertRaises(M.PaperShadowContractError): M.PaperShadowDecision("run", "r", "e", M.SimulationMode.PAPER, M.SimulationDisposition.DISABLED, Decimal("0"), Decimal("0"), "disabled", UTC)


if __name__ == "__main__": unittest.main()
