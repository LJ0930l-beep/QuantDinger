import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)


def load():
    names = [
        "app", "app.domain", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts", "app.domain.gate_market_payload_contracts",
    ]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        paths = {names[2]: ROOT / "app/domain/multi_asset_capability_contracts.py", names[3]: ROOT / "app/domain/gate_market_read_contracts.py", names[4]: ROOT / "app/domain/gate_market_payload_contracts.py"}
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[4]], sys.modules[names[2]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, CAP = load()


class GateMarketPayloadTests(unittest.TestCase):
    def test_candles_use_official_order_and_decimal_values(self):
        rows = [["1767225600", "1000", "101", "102", "99", "100", "10", True]]
        facts = M.normalize_gate_candles(rows, market_type=CAP.AssetMarketType.SPOT, instrument_id="BTC_USDT", interval="1m", observed_at=UTC, source_event_prefix="gate-candle", snapshot_id="snap-1", rule_version="rules-1", evidence_hash_prefix="ev")
        self.assertEqual(facts[0].open_price, 100)
        self.assertEqual(facts[0].sequence, 1767225600)
        self.assertEqual(facts[0].occurred_at.tzinfo, timezone.utc)

    def test_current_forming_candle_is_skipped_when_closed_evidence_exists(self):
        rows = [
            ["1767225600", "1000", "101", "102", "99", "100", "10", "true"],
            ["1767225660", "1000", "101", "102", "99", "100", "10", "false"],
        ]
        facts = M.normalize_gate_candles(
            rows,
            market_type=CAP.AssetMarketType.SPOT,
            instrument_id="BTC_USDT",
            interval="1m",
            observed_at=UTC,
            source_event_prefix="gate-candle",
            snapshot_id="snap-1",
            rule_version="rules-1",
            evidence_hash_prefix="ev",
        )
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].sequence, 1767225600)

    def test_perpetual_object_candles_are_normalized(self):
        rows = [
            {"t": 1767225600, "o": "100", "h": "102", "l": "99", "c": "101", "v": "10", "sum": "1000"},
            {"t": 1767225660, "o": "101", "h": "103", "l": "100", "c": "102", "v": "11", "sum": "1100"},
        ]
        facts = M.normalize_gate_candles(
            rows,
            market_type=CAP.AssetMarketType.PERPETUAL,
            instrument_id="BTC_USDT",
            interval="1m",
            observed_at=UTC,
            source_event_prefix="gate-perp-candle",
            snapshot_id="snap-perp",
            rule_version="rules-1",
            evidence_hash_prefix="ev",
        )
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0].open_price, 100)
        self.assertEqual(facts[1].close_price, 102)

    def test_unknown_closed_flag_encoding_fails_closed(self):
        with self.assertRaises(M.GateMarketPayloadError):
            M.normalize_gate_candles(
                [["1767225600", "1", "1", "1", "1", "1", "1", 1]],
                market_type=CAP.AssetMarketType.SPOT,
                instrument_id="BTC_USDT",
                interval="1m",
                observed_at=UTC,
                source_event_prefix="x",
                snapshot_id="s",
                rule_version="r",
                evidence_hash_prefix="e",
            )

    def test_open_or_out_of_order_candles_fail_closed(self):
        with self.assertRaises(M.GateMarketPayloadError):
            M.normalize_gate_candles([["1767225600", "1", "1", "1", "1", "1", "1", False]], market_type=CAP.AssetMarketType.SPOT, instrument_id="BTC_USDT", interval="1m", observed_at=UTC, source_event_prefix="x", snapshot_id="s", rule_version="r", evidence_hash_prefix="e")
        with self.assertRaises(M.GateMarketPayloadError):
            M.normalize_gate_candles([[1767225600, "1", "1", "1", "1", "1", "1", True], [1767225600, "1", "1", "1", "1", "1", "1", True]], market_type=CAP.AssetMarketType.SPOT, instrument_id="BTC_USDT", interval="1m", observed_at=UTC, source_event_prefix="x", snapshot_id="s", rule_version="r", evidence_hash_prefix="e")

    def test_candle_time_gap_fails_closed(self):
        with self.assertRaises(M.GateMarketPayloadError):
            M.normalize_gate_candles(
                [
                    [1767225600, "1", "1", "1", "1", "1", "1", True],
                    [1767225720, "1", "1", "1", "1", "1", "1", True],
                ],
                market_type=CAP.AssetMarketType.SPOT,
                instrument_id="BTC_USDT",
                interval="1m",
                observed_at=UTC,
                source_event_prefix="x",
                snapshot_id="s",
                rule_version="r",
                evidence_hash_prefix="e",
            )

    def test_order_book_preserves_update_and_current_times(self):
        payload = {"id": 7, "current": 1767225720000, "update": 1767225719000, "bids": [["100", "1"]], "asks": [["101", "2"]]}
        fact = M.normalize_gate_order_book(payload, market_type=CAP.AssetMarketType.PERPETUAL, instrument_id="BTC_USDT", source_event_prefix="depth", snapshot_id="s", rule_version="r", evidence_hash_prefix="e")
        self.assertEqual(fact.sequence, 7)
        self.assertLess(fact.occurred_at, fact.observed_at)
        self.assertIsNone(fact.depth_limit)
        with self.assertRaises(M.GateMarketPayloadError):
            M.normalize_gate_order_book(
                payload,
                market_type=CAP.AssetMarketType.PERPETUAL,
                instrument_id="BTC_USDT",
                source_event_prefix="depth",
                snapshot_id="s",
                rule_version="r",
                evidence_hash_prefix="e",
                depth_limit=True,
            )

    def test_perpetual_order_book_accepts_fractional_seconds_without_id(self):
        payload = {
            "current": 1767225720.500,
            "update": 1767225719.250,
            "bids": [["100", "1"]],
            "asks": [["101", "2"]],
        }
        fact = M.normalize_gate_order_book(
            payload,
            market_type=CAP.AssetMarketType.PERPETUAL,
            instrument_id="BTC_USDT",
            source_event_prefix="depth",
            snapshot_id="s",
            rule_version="r",
            evidence_hash_prefix="e",
        )
        self.assertEqual(fact.sequence, 1767225719250)

    def test_perpetual_order_book_accepts_object_levels(self):
        payload = {
            "current": 1767225720000,
            "update": 1767225719000,
            "bids": [{"p": "100", "s": 2}],
            "asks": [{"p": "101", "s": 3}],
        }
        fact = M.normalize_gate_order_book(
            payload,
            market_type=CAP.AssetMarketType.PERPETUAL,
            instrument_id="BTC_USDT",
            source_event_prefix="depth",
            snapshot_id="s",
            rule_version="r",
            evidence_hash_prefix="e",
        )
        self.assertEqual(fact.bids[0].quantity, 2)

    def test_crossed_or_ambiguous_order_book_fails_closed(self):
        payload = {"id": 7, "current": 1000, "update": 1001, "bids": [["101", "1"]], "asks": [["100", "2"]]}
        with self.assertRaises(M.GateMarketPayloadError):
            M.normalize_gate_order_book(payload, market_type=CAP.AssetMarketType.SPOT, instrument_id="BTC_USDT", source_event_prefix="depth", snapshot_id="s", rule_version="r", evidence_hash_prefix="e")


if __name__ == "__main__":
    unittest.main()
