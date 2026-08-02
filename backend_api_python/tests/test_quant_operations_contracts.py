"""Read-only operational posture tests; no database, network, or credentials."""

import importlib.util
import sys
import unittest
from enum import Enum
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = {
        "app": None,
        "app.domain": None,
        "app.domain.backtest_result_contracts": None,
        "app.domain.paper_shadow_run_result_contracts": None,
        "app.domain.gate_testnet_readiness_contracts": ROOT / "app/domain/gate_testnet_readiness_contracts.py",
        "app.domain.research_readiness_contracts": ROOT / "app/domain/research_readiness_contracts.py",
        "app.domain.production_readiness_contracts": ROOT / "app/domain/production_readiness_contracts.py",
        "app.domain.gate_testnet_rehearsal_contracts": ROOT / "app/domain/gate_testnet_rehearsal_contracts.py",
        "app.domain.quant_operations_contracts": ROOT / "app/domain/quant_operations_contracts.py",
        "app.services": None,
        "app.services.quant_operations_service": ROOT / "app/services/quant_operations_service.py",
    }
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
        backtest = ModuleType("app.domain.backtest_result_contracts"); backtest.BacktestResultStatus = BacktestStatus
        paper = ModuleType("app.domain.paper_shadow_run_result_contracts"); paper.PaperShadowRunStatus = PaperStatus
        sys.modules["app.domain.backtest_result_contracts"] = backtest
        sys.modules["app.domain.paper_shadow_run_result_contracts"] = paper
        for name in (
            "app.domain.gate_testnet_readiness_contracts",
            "app.domain.research_readiness_contracts",
            "app.domain.production_readiness_contracts",
            "app.domain.gate_testnet_rehearsal_contracts",
            "app.domain.quant_operations_contracts",
        ):
            spec = importlib.util.spec_from_file_location(name, names[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        name = "app.services.quant_operations_service"
        spec = importlib.util.spec_from_file_location(name, names[name])
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules["app.domain.quant_operations_contracts"], sys.modules["app.domain.research_readiness_contracts"], sys.modules["app.domain.gate_testnet_readiness_contracts"], sys.modules["app.domain.production_readiness_contracts"], sys.modules["app.domain.gate_testnet_rehearsal_contracts"], sys.modules[name], BacktestStatus, PaperStatus
    finally:
        for name, original in old.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


M, Research, Gate, Production, Rehearsal, Service, BacktestStatus, PaperStatus = _load()


def _facts(*, rehearsal_status=None, production_overrides=None):
    research = Research.derive_research_readiness(Gate.GateTestnetReadinessStatus.READY, BacktestStatus.READY, PaperStatus.COMPLETED)
    values = dict(
        backend_ci_passed=True, security_ci_passed=True, architecture_guard_passed=True,
        schema_parity_passed=True, recovery_tests_passed=True, testnet_read_passed=True,
        rollback_plan_verified=True, operator_approval=True, live_enabled=False,
    )
    values.update(production_overrides or {})
    evidence = Production.ProductionReadinessEvidence(**values)
    if rehearsal_status is None:
        rehearsal = Rehearsal.GateTestnetRehearsalResult(
            Rehearsal.GateTestnetRehearsalStatus.READY,
            (Rehearsal.GateTestnetRehearsalSnapshot("snap-1", "session-1", "BTC_USDT", __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc), "dataset-1"),),
        )
    else:
        rehearsal = Rehearsal.GateTestnetRehearsalResult(rehearsal_status)
    return research, evidence, rehearsal


class QuantOperationsContractTests(unittest.TestCase):
    def test_all_ready_is_non_live_and_deterministic(self):
        facts = _facts()
        first = M.derive_quant_operations(*facts)
        second = M.derive_quant_operations(*facts)
        self.assertEqual(first.status, M.QuantOperationsStatus.PRODUCTION_READY)
        self.assertEqual(first.operations_fingerprint, second.operations_fingerprint)
        self.assertFalse(first.to_public_dict()["live_enabled"])

    def test_rehearsal_or_research_failure_blocks(self):
        facts = _facts(rehearsal_status=Rehearsal.GateTestnetRehearsalStatus.FAILED)
        result = M.derive_quant_operations(*facts)
        self.assertEqual(result.status, M.QuantOperationsStatus.BLOCKED)
        self.assertIn("rehearsal_failed", result.reason_codes)

    def test_missing_recovery_stops_at_testnet(self):
        facts = _facts(production_overrides={"recovery_tests_passed": False})
        result = M.derive_quant_operations(*facts)
        self.assertEqual(result.status, M.QuantOperationsStatus.TESTNET_READY)

    def test_invalid_live_evidence_fails_closed(self):
        with self.assertRaises(Production.ProductionReadinessError):
            _facts(production_overrides={"live_enabled": True})

    def test_service_is_read_only_and_typed(self):
        snapshot = M.derive_quant_operations(*_facts())
        status, body = Service.QuantOperationsService(lambda: snapshot).read_response()
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "PRODUCTION_READY")
        self.assertFalse(body["live_enabled"])
        with self.assertRaises(Service.QuantOperationsServiceError):
            Service.QuantOperationsService(lambda: {"live_enabled": True}).read_response()
        self.assertEqual(Service.QuantOperationsService().read_response()[0], 503)


if __name__ == "__main__":
    unittest.main()
