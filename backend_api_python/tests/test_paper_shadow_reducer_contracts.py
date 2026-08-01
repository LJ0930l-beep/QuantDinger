import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    names = ["app", "app.domain", "app.domain.paper_shadow_contracts", "app.domain.paper_shadow_reducer_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/paper_shadow_contracts.py"), (names[3], ROOT / "app/domain/paper_shadow_reducer_contracts.py")):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[3]], sys.modules[names[2]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, PS = load()


def decision(request="request-1", economic="economic-1", *, quantity="1", disposition=None, reason="accepted"):
    return PS.PaperShadowDecision("run-1", request, economic, PS.SimulationMode.PAPER, disposition or PS.SimulationDisposition.ACCEPTED, Decimal(quantity), Decimal("10"), reason, UTC)


class PaperShadowReducerTests(unittest.TestCase):
    def test_created_then_exact_replay(self):
        empty = M.PaperShadowDecisionSet("run-1", PS.SimulationMode.PAPER)
        first = M.record_paper_shadow_decision(empty, decision())
        self.assertEqual(first.disposition, M.SimulationRecordDisposition.CREATED)
        replay = M.record_paper_shadow_decision(first.decision_set, decision())
        self.assertEqual(replay.disposition, M.SimulationRecordDisposition.REPLAYED)
        self.assertEqual(replay.decision_set.replay_fingerprint, first.decision_set.replay_fingerprint)

    def test_same_request_with_changed_immutable_fact_is_conflict(self):
        state = M.record_paper_shadow_decision(M.PaperShadowDecisionSet("run-1", PS.SimulationMode.PAPER), decision()).decision_set
        result = M.record_paper_shadow_decision(state, decision(quantity="2"))
        self.assertEqual(result.disposition, M.SimulationRecordDisposition.CONFLICT)
        self.assertEqual(len(result.decision_set.decisions), 1)

    def test_different_request_can_be_created_and_fingerprint_changes(self):
        empty = M.PaperShadowDecisionSet("run-1", PS.SimulationMode.PAPER)
        first = M.record_paper_shadow_decision(empty, decision()).decision_set
        second = M.record_paper_shadow_decision(first, decision(request="request-2", economic="economic-2")).decision_set
        self.assertEqual(len(second.decisions), 2)
        self.assertNotEqual(first.replay_fingerprint, second.replay_fingerprint)

    def test_mode_and_run_scope_are_fail_closed(self):
        with self.assertRaises(M.PaperShadowReducerError):
            M.PaperShadowDecisionSet("run-1", PS.SimulationMode.DISABLED)
        empty = M.PaperShadowDecisionSet("run-1", PS.SimulationMode.SHADOW)
        with self.assertRaises(M.PaperShadowReducerError):
            M.record_paper_shadow_decision(empty, decision())

    def test_disposition_and_reason_are_part_of_replay_facts(self):
        empty = M.PaperShadowDecisionSet("run-1", PS.SimulationMode.PAPER)
        accepted = M.record_paper_shadow_decision(empty, decision()).decision_set
        rejected = decision(disposition=PS.SimulationDisposition.REJECTED, reason="risk denied")
        result = M.record_paper_shadow_decision(accepted, rejected)
        self.assertEqual(result.disposition, M.SimulationRecordDisposition.CONFLICT)

    def test_set_is_immutable(self):
        state = M.PaperShadowDecisionSet("run-1", PS.SimulationMode.PAPER)
        with self.assertRaises((AttributeError, TypeError)):
            state.mode = PS.SimulationMode.SHADOW


if __name__ == "__main__":
    unittest.main()
