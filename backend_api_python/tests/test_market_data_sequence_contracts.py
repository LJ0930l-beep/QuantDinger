"""Pure tests for deterministic market-data sequence application."""

from __future__ import annotations

from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contracts() -> SimpleNamespace:
    names = ("app", "app.domain", "app.domain.market_data_quality_contracts", "app.domain.market_data_sequence_contracts")
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        quality = _load(names[2], ROOT / "app" / "domain" / "market_data_quality_contracts.py")
        sequence = _load(names[3], ROOT / "app" / "domain" / "market_data_sequence_contracts.py")
        return SimpleNamespace(quality=quality, sequence=sequence)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = original


C = _contracts()
UTC = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def event(sequence: int, event_id: str = "event-1", payload: str = "payload-1"):
    return C.quality.MarketDataEventFact(event_id, "gate", "BTC_USDT", UTC, UTC, sequence, "dataset-1", "rules-v1", payload)


class MarketDataSequenceContractTests(unittest.TestCase):
    def test_contiguous_apply_and_exact_replay(self):
        state = C.sequence.MarketDataSequenceState("gate", "BTC_USDT", "dataset-1", "rules-v1")
        first = C.sequence.apply_market_data_event(state, event(0))
        replay = C.sequence.apply_market_data_event(first.state, event(0))
        self.assertEqual(first.disposition, C.sequence.SequenceDisposition.APPENDED)
        self.assertEqual(replay.disposition, C.sequence.SequenceDisposition.REPLAYED)
        self.assertEqual(first.state.state_fingerprint, replay.state.state_fingerprint)

    def test_gap_is_not_silently_accepted(self):
        state = C.sequence.MarketDataSequenceState("gate", "BTC_USDT", "dataset-1", "rules-v1")
        result = C.sequence.apply_market_data_event(state, event(2))
        self.assertEqual(result.disposition, C.sequence.SequenceDisposition.GAP)
        self.assertEqual(result.state.next_sequence, 0)

    def test_out_of_order_and_scope_mismatch_fail_closed(self):
        state = C.sequence.MarketDataSequenceState("gate", "BTC_USDT", "dataset-1", "rules-v1", next_sequence=1, accepted_event_ids=("event-0",))
        self.assertEqual(C.sequence.apply_market_data_event(state, event(0, "event-old")).disposition, C.sequence.SequenceDisposition.CONFLICT)
        with self.assertRaises(C.sequence.MarketDataSequenceError):
            C.sequence.apply_market_data_event(state, event(1, "event-new").__class__("event-new", "other", "BTC_USDT", UTC, UTC, 1, "dataset-1", "rules-v1", "payload"))

    def test_duplicate_identity_at_future_sequence_is_typed_error(self):
        state = C.sequence.MarketDataSequenceState("gate", "BTC_USDT", "dataset-1", "rules-v1", next_sequence=1, accepted_event_ids=("event-1",))
        with self.assertRaises(C.sequence.MarketDataSequenceError):
            C.sequence.apply_market_data_event(state, event(1, "event-1"))


if __name__ == "__main__":
    unittest.main()
