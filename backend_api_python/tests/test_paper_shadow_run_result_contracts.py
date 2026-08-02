"""Pure lifecycle and read-only result tests for Paper/Shadow runs."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contracts() -> SimpleNamespace:
    names = (
        "app",
        "app.domain",
        "app.domain.paper_shadow_contracts",
        "app.domain.paper_shadow_reducer_contracts",
        "app.domain.paper_shadow_run_result_contracts",
        "app.services",
        "app.services.paper_shadow_result_service",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services")
        services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app
        sys.modules["app.domain"] = domain
        sys.modules["app.services"] = services
        paper = _load(names[2], ROOT / "app" / "domain" / "paper_shadow_contracts.py")
        reducer = _load(names[3], ROOT / "app" / "domain" / "paper_shadow_reducer_contracts.py")
        result = _load(names[4], ROOT / "app" / "domain" / "paper_shadow_run_result_contracts.py")
        service = _load(names[6], ROOT / "app" / "services" / "paper_shadow_result_service.py")
        return SimpleNamespace(paper=paper, reducer=reducer, result=result, service=service)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


C = _contracts()


def _run():
    return C.paper.PaperShadowRunFacts(
        "run-1", C.paper.SimulationMode.PAPER, "dataset-1", "strategy-1", "risk-1", "tolerance-1", UTC,
    )


class PaperShadowRunResultTests(unittest.TestCase):
    def test_running_result_is_scoped_and_safe(self):
        run = _run()
        result = C.result.PaperShadowRunResult(run, C.reducer.PaperShadowDecisionSet("run-1", C.paper.SimulationMode.PAPER), C.result.PaperShadowRunStatus.RUNNING)
        self.assertEqual(result.decision_count, 0)
        self.assertEqual(result.accepted_count, 0)
        self.assertEqual(result.rejected_count, 0)
        self.assertEqual(result.to_public_dict()["status"], "RUNNING")

    def test_completed_result_requires_terminal_timestamp_and_is_deterministic(self):
        run = _run()
        decision_set = C.reducer.PaperShadowDecisionSet("run-1", C.paper.SimulationMode.PAPER)
        with self.assertRaises(C.result.PaperShadowRunResultError):
            C.result.PaperShadowRunResult(run, decision_set, C.result.PaperShadowRunStatus.COMPLETED)
        first = C.result.PaperShadowRunResult(run, decision_set, C.result.PaperShadowRunStatus.COMPLETED, UTC + timedelta(minutes=1))
        second = C.result.PaperShadowRunResult(run, decision_set, C.result.PaperShadowRunStatus.COMPLETED, UTC + timedelta(minutes=1))
        self.assertEqual(first.result_fingerprint, second.result_fingerprint)

    def test_failed_result_requires_reason_and_does_not_expose_payload(self):
        run = _run()
        decision_set = C.reducer.PaperShadowDecisionSet("run-1", C.paper.SimulationMode.PAPER)
        with self.assertRaises(C.result.PaperShadowRunResultError):
            C.result.PaperShadowRunResult(run, decision_set, C.result.PaperShadowRunStatus.FAILED, UTC + timedelta(minutes=1))
        failed = C.result.PaperShadowRunResult(run, decision_set, C.result.PaperShadowRunStatus.FAILED, UTC + timedelta(minutes=1), "dataset unavailable")
        self.assertEqual(failed.to_public_dict()["failure_reason"], "dataset unavailable")
        self.assertNotIn("secret", repr(failed.to_public_dict()))

    def test_service_returns_unavailable_unauthorized_and_ready_states(self):
        run = _run()
        decision_set = C.reducer.PaperShadowDecisionSet("run-1", C.paper.SimulationMode.PAPER)
        completed = C.result.PaperShadowRunResult(run, decision_set, C.result.PaperShadowRunStatus.COMPLETED, UTC + timedelta(minutes=1))
        self.assertEqual(C.service.PaperShadowResultService().read_response()[0], 503)
        self.assertEqual(C.service.PaperShadowResultService(lambda: completed).read_response(authorized=False)[0], 401)
        status, body = C.service.PaperShadowResultService(lambda: completed).read_response()
        self.assertEqual(status, 200)
        self.assertEqual(body["decision_count"], 0)


if __name__ == "__main__":
    unittest.main()
