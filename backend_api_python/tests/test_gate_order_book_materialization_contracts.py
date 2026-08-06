"""Pure materialized-depth tests for verified Gate stream frames."""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc)


def load():
    names = (
        "app",
        "app.domain",
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts",
        "app.domain.gate_order_book_stream_contracts",
        "app.domain.gate_order_book_stream_payload_contracts",
        "app.domain.gate_order_book_materialization_contracts",
    )
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        paths = {
            names[2]: ROOT / "app/domain/multi_asset_capability_contracts.py",
            names[3]: ROOT / "app/domain/gate_market_read_contracts.py",
            names[4]: ROOT / "app/domain/gate_order_book_stream_contracts.py",
            names[5]: ROOT / "app/domain/gate_order_book_stream_payload_contracts.py",
            names[6]: ROOT / "app/domain/gate_order_book_materialization_contracts.py",
        }
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return tuple(sys.modules[name] for name in names[2:])
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


CAP, MARKET, STREAM, PAYLOAD, MATERIAL = load()


def subscription(*, market_type=None):
    return STREAM.GateOrderBookStreamSubscription(
        market_type=market_type or CAP.AssetMarketType.SPOT,
        instrument_id="BTC_USDT",
        snapshot_id="materialized-snapshot-1",
        rule_version="gate-ws-rule-v1",
        depth_limit=20,
        update_interval="20ms",
    )


def snapshot(*, market_type=None):
    return MARKET.GateOrderBookSnapshot(
        market_type=market_type or CAP.AssetMarketType.SPOT,
        instrument_id="BTC_USDT",
        bids=(
            MARKET.GateOrderBookLevel(Decimal("100"), Decimal("1")),
            MARKET.GateOrderBookLevel(Decimal("99"), Decimal("2")),
        ),
        asks=(
            MARKET.GateOrderBookLevel(Decimal("101"), Decimal("1")),
            MARKET.GateOrderBookLevel(Decimal("102"), Decimal("2")),
        ),
        occurred_at=UTC,
        observed_at=UTC,
        sequence=100,
        source_event_id="rest:100",
        snapshot_id="materialized-snapshot-1",
        rule_version="gate-ws-rule-v1",
        evidence_hash="a" * 64,
        depth_limit=20,
    )


def spot_frame(*, first=101, last=101, full=False, bids=None, asks=None):
    result = {
        "t": "1780070400000",
        "l": "20",
        "s": "BTC_USDT",
        "U": first,
        "u": last,
        "b": [["100", "2"], ["99", "0"]] if bids is None else bids,
        "a": [["101", "0"], ["100.5", "3"]] if asks is None else asks,
    }
    if full:
        result["full"] = True
    return {"channel": "spot.order_book_update", "event": "update", "error": None, "result": result}


def frame(payload, *, subject):
    return PAYLOAD.normalize_gate_order_book_update_frame(
        payload,
        subscription=subject,
        observed_at=UTC + timedelta(seconds=5),
        source_event_prefix="gate-ws",
    )


class GateOrderBookMaterializationContractTests(unittest.TestCase):
    def test_incremental_absolute_levels_are_applied_without_mutating_prior_state(self):
        subject = subscription()
        initial = MATERIAL.gate_order_book_materialized_state_from_snapshot(snapshot(), subject)
        result = MATERIAL.apply_gate_order_book_materialized_frame(initial, frame(spot_frame(), subject=subject))
        self.assertEqual(result.disposition, MATERIAL.GateOrderBookMaterializationDisposition.APPLIED)
        self.assertEqual(initial.snapshot.sequence, 100)
        self.assertEqual(result.state.snapshot.sequence, 101)
        self.assertEqual([(x.price, x.quantity) for x in result.state.snapshot.bids], [(Decimal("100"), Decimal("2"))])
        self.assertEqual(
            [(x.price, x.quantity) for x in result.state.snapshot.asks],
            [(Decimal("100.5"), Decimal("3")), (Decimal("102"), Decimal("2"))],
        )

    def test_full_frame_replaces_depth_and_can_reanchor_after_gap(self):
        subject = subscription()
        initial = MATERIAL.gate_order_book_materialized_state_from_snapshot(snapshot(), subject)
        full = frame(
            spot_frame(
                first=105,
                last=108,
                full=True,
                bids=[["100.25", "4"]],
                asks=[["100.75", "5"]],
            ),
            subject=subject,
        )
        result = MATERIAL.apply_gate_order_book_materialized_frame(initial, full)
        self.assertEqual(result.disposition, MATERIAL.GateOrderBookMaterializationDisposition.APPLIED)
        self.assertEqual(result.reason, "full_depth_snapshot_reanchors_stream")
        self.assertEqual(result.state.snapshot.sequence, 108)
        self.assertEqual([(x.price, x.quantity) for x in result.state.snapshot.bids], [(Decimal("100.25"), Decimal("4"))])
        self.assertEqual([(x.price, x.quantity) for x in result.state.snapshot.asks], [(Decimal("100.75"), Decimal("5"))])

    def test_empty_incremental_update_advances_perpetual_sequence_without_changing_depth(self):
        subject = subscription(market_type=CAP.AssetMarketType.PERPETUAL)
        initial = MATERIAL.gate_order_book_materialized_state_from_snapshot(
            snapshot(market_type=CAP.AssetMarketType.PERPETUAL), subject,
        )
        raw = {
            "channel": "futures.order_book_update",
            "event": "update",
            "error": None,
            "result": {"t": "1780070400000", "l": "20", "s": "BTC_USDT", "U": 101, "u": 101, "b": [], "a": []},
        }
        applied = MATERIAL.apply_gate_order_book_materialized_frame(initial, frame(raw, subject=subject))
        self.assertEqual(applied.disposition, MATERIAL.GateOrderBookMaterializationDisposition.APPLIED)
        self.assertEqual(applied.state.snapshot.sequence, 101)
        self.assertEqual(applied.state.snapshot.bids, initial.snapshot.bids)
        self.assertEqual(applied.state.snapshot.asks, initial.snapshot.asks)

    def test_invalid_or_gapped_materialization_preserves_prior_book_and_requests_reseed(self):
        subject = subscription()
        initial = MATERIAL.gate_order_book_materialized_state_from_snapshot(snapshot(), subject)
        empty_bid = frame(spot_frame(bids=[["100", "0"], ["99", "0"]], asks=[]), subject=subject)
        unsafe = MATERIAL.apply_gate_order_book_materialized_frame(initial, empty_bid)
        self.assertEqual(unsafe.disposition, MATERIAL.GateOrderBookMaterializationDisposition.RESEED_REQUIRED)
        self.assertEqual(unsafe.state.materialization_fingerprint, initial.materialization_fingerprint)
        gap = frame(spot_frame(first=103, last=104), subject=subject)
        gapped = MATERIAL.apply_gate_order_book_materialized_frame(initial, gap)
        self.assertEqual(gapped.disposition, MATERIAL.GateOrderBookMaterializationDisposition.RESEED_REQUIRED)
        self.assertEqual(gapped.state.materialization_fingerprint, initial.materialization_fingerprint)

    def test_exact_replay_is_idempotent(self):
        subject = subscription()
        initial = MATERIAL.gate_order_book_materialized_state_from_snapshot(snapshot(), subject)
        update = frame(spot_frame(), subject=subject)
        first = MATERIAL.apply_gate_order_book_materialized_frame(initial, update)
        replay = MATERIAL.apply_gate_order_book_materialized_frame(first.state, update)
        self.assertEqual(replay.disposition, MATERIAL.GateOrderBookMaterializationDisposition.REPLAYED)
        self.assertEqual(replay.state.materialization_fingerprint, first.state.materialization_fingerprint)


if __name__ == "__main__":
    unittest.main()
