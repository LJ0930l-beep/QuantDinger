import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]; UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    name = "app.domain.market_data_quality_contracts"; names = ["app", "app.domain", name]; old = {n: sys.modules.get(n) for n in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]; sys.modules["app"] = app; sys.modules["app.domain"] = domain
        spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domain" / "market_data_quality_contracts.py"); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
    finally:
        for n in reversed(names):
            if old[n] is None: sys.modules.pop(n, None)
            else: sys.modules[n] = old[n]


M = load()


def event(**changes):
    facts = dict(event_id="e1", source="gate-public", instrument_id="BTC_USDT", occurred_at=UTC, observed_at=UTC, sequence=1, dataset_snapshot_id="d1", rule_version="r1", payload_fingerprint="p1")
    facts.update(changes); return M.MarketDataEventFact(**facts)


class DataQualityTests(unittest.TestCase):
    def test_point_in_time_accepts_and_is_replayable(self):
        assessment = M.assess_point_in_time((event(),), as_of=UTC); self.assertEqual(assessment.status, M.DataQualityStatus.COMPLETE); self.assertEqual(assessment.assessment_fingerprint, M.assess_point_in_time((event(),), as_of=UTC).assessment_fingerprint)

    def test_future_event_is_late_and_not_accepted(self):
        assessment = M.assess_point_in_time((event(observed_at=UTC + timedelta(seconds=1)),), as_of=UTC); self.assertEqual(assessment.status, M.DataQualityStatus.LATE); self.assertEqual(assessment.accepted_events, ())

    def test_duplicate_and_conflict_are_distinct(self):
        duplicate = M.assess_point_in_time((event(), event(event_id="e2")), as_of=UTC); self.assertEqual(duplicate.status, M.DataQualityStatus.DUPLICATE)
        conflict = M.assess_point_in_time((event(), event(event_id="e2", payload_fingerprint="p2")), as_of=UTC); self.assertEqual(conflict.status, M.DataQualityStatus.CONFLICT)

    def test_out_of_order_and_missing(self):
        result = M.assess_point_in_time((event(sequence=2), event(event_id="e2", sequence=1)), as_of=UTC); self.assertEqual(result.status, M.DataQualityStatus.OUT_OF_ORDER)
        self.assertEqual(M.assess_point_in_time((), as_of=UTC).status, M.DataQualityStatus.MISSING)

    def test_expected_sequence_gap_is_not_complete(self):
        result = M.assess_point_in_time((event(sequence=1),), as_of=UTC, expected_sequences=(1, 2))
        self.assertEqual(result.status, M.DataQualityStatus.GAP)

    def test_same_event_id_at_different_sequence_is_conflict(self):
        result = M.assess_point_in_time((event(), event(sequence=2)), as_of=UTC)
        self.assertEqual(result.status, M.DataQualityStatus.CONFLICT)
        self.assertEqual(result.accepted_events, (event(),))
        self.assertEqual(result.rejected_event_ids, ("e1",))

    def test_same_event_id_with_changed_payload_is_conflict(self):
        result = M.assess_point_in_time((event(), event(payload_fingerprint="p2")), as_of=UTC)
        self.assertEqual(result.status, M.DataQualityStatus.CONFLICT)
        self.assertEqual(result.rejected_event_ids, ("e1",))

    def test_disconnect_is_explicit_even_for_contiguous_rows(self):
        result = M.assess_point_in_time((event(),), as_of=UTC, expected_sequences=(1,), disconnected=True)
        self.assertEqual(result.status, M.DataQualityStatus.DISCONNECTED)

    def test_expected_sequences_are_strictly_canonical(self):
        with self.assertRaises(M.MarketDataQualityError):
            M.assess_point_in_time((event(),), as_of=UTC, expected_sequences=(2, 1))

    def test_event_scope_and_time_are_strict(self):
        with self.assertRaises(M.MarketDataQualityError): event(observed_at=datetime(2026, 1, 1))
        with self.assertRaises(M.MarketDataQualityError): event(sequence=-1)


if __name__ == "__main__": unittest.main()
