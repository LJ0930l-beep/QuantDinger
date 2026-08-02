"""Readiness service tests with injected typed facts only."""

import importlib.util
import sys
import unittest
from enum import Enum
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load():
    names = [
        "app", "app.domain", "app.domain.gate_testnet_readiness_contracts",
        "app.domain.backtest_result_contracts", "app.domain.paper_shadow_run_result_contracts",
        "app.domain.research_readiness_contracts", "app.services", "app.services.research_readiness_service",
    ]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services

        class BacktestStatus(str, Enum):
            READY = "READY"; UNAVAILABLE = "UNAVAILABLE"; UNAUTHORIZED = "UNAUTHORIZED"

        class PaperStatus(str, Enum):
            RUNNING = "RUNNING"; COMPLETED = "COMPLETED"; FAILED = "FAILED"

        backtest = ModuleType(names[3]); backtest.BacktestResultStatus = BacktestStatus
        paper = ModuleType(names[4]); paper.PaperShadowRunStatus = PaperStatus
        sys.modules[names[3]] = backtest; sys.modules[names[4]] = paper
        for name in (names[2], names[5], names[7]):
            path = ROOT / ("app/domain/gate_testnet_readiness_contracts.py" if name == names[2] else "app/domain/research_readiness_contracts.py" if name == names[5] else "app/services/research_readiness_service.py")
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[7]], sys.modules[names[2]], BacktestStatus, PaperStatus
    finally:
        for name, original in old.items():
            if original is None: sys.modules.pop(name, None)
            else: sys.modules[name] = original


M, GateModule, BacktestStatus, PaperStatus = load()
GateStatus = GateModule.GateTestnetReadinessStatus


class ResearchReadinessServiceTests(unittest.TestCase):
    def test_injected_facts_produce_ready_response(self):
        service = M.ResearchReadinessService(lambda: GateStatus.READY, lambda: BacktestStatus.READY, lambda: PaperStatus.COMPLETED)
        status, body = service.read_response()
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "READY")
        self.assertFalse(body["live_enabled"])

    def test_missing_provider_is_unavailable_without_side_effects(self):
        status, body = M.ResearchReadinessService().read_response()
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "UNAVAILABLE")

    def test_invalid_provider_is_typed_and_not_leaked(self):
        service = M.ResearchReadinessService(lambda: "READY", lambda: BacktestStatus.READY)
        with self.assertRaises(M.ResearchReadinessServiceError) as context:
            service.read_view()
        self.assertEqual(str(context.exception), "readiness providers returned invalid facts")

    def test_unauthorized_is_non_live(self):
        status, body = M.ResearchReadinessService().read_response(authorized=False)
        self.assertEqual(status, 401)
        self.assertFalse(body["live_enabled"])


if __name__ == "__main__":
    unittest.main()
