"""Pure Gate REST-snapshot plus WebSocket delta sequencing tests."""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc)


def load():
    names = (
        "app",
        "app.domain",
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts",
        "app.domain.gate_order_book_stream_contracts",
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
        }
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[2]], sys.modules[names[3]], sys.modules[names[4]]
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


CAP, MARKET, STREAM = load()


def fingerprint(number: int) -> str:
    return f"{number:064x}"


def snapshot(*, update_id: int = 100, instrument_id: str = "BTC_USDT"):
    return MARKET.GateOrderBookSnapshot(
        market_type=CAP.AssetMarketType.SPOT,
        instrument_id=instrument_id,
        bids=(MARKET.GateOrderBookLevel(Decimal("100"), Decimal("1")),),
        asks=(MARKET.GateOrderBookLevel(Decimal("101"), Decimal("1")),),
        occurred_at=UTC,
        observed_at=UTC,
        sequence=update_id,
        source_event_id=f"rest:{update_id}",
        snapshot_id="gate-snapshot-1",
        rule_version="gate-rules-v1",
        evidence_hash=fingerprint(update_id),
        depth_limit=20,
    )


def subscription(*, market_type=None, instrument_id: str = "BTC_USDT"):
    return STREAM.GateOrderBookStreamSubscription(
        market_type=market_type or CAP.AssetMarketType.SPOT,
        instrument_id=instrument_id,
        snapshot_id="gate-snapshot-1",
        rule_version="gate-rules-v1",
        depth_limit=20,
        update_interval="20ms",
    )


def delta(first: int, last: int, *, event_id: str = "ws:1", payload: int = 1, instrument_id: str = "BTC_USDT"):
    return STREAM.GateOrderBookDelta(
        market_type=CAP.AssetMarketType.SPOT,
        instrument_id=instrument_id,
        first_update_id=first,
        last_update_id=last,
        bids=(STREAM.GateOrderBookDeltaLevel(Decimal("100"), Decimal("2")),),
        asks=(STREAM.GateOrderBookDeltaLevel(Decimal("101"), Decimal("0")),),
        occurred_at=UTC + timedelta(seconds=1),
        observed_at=UTC + timedelta(seconds=2),
        source_event_id=event_id,
        snapshot_id="gate-snapshot-1",
        rule_version="gate-rules-v1",
        payload_fingerprint=fingerprint(payload),
    )


class GateOrderBookStreamContractTests(unittest.TestCase):
    def test_rest_baseline_first_delta_and_exact_replay_are_deterministic(self):
        state = STREAM.gate_order_book_stream_state_from_snapshot(snapshot(), subscription())
        self.assertEqual(state.next_update_id, 101)
        update = delta(99, 101)
        applied = STREAM.apply_gate_order_book_delta(state, update)
        self.assertEqual(applied.disposition, STREAM.GateOrderBookStreamDisposition.APPLIED)
        self.assertEqual(applied.state.next_update_id, 102)
        replay = STREAM.apply_gate_order_book_delta(applied.state, update)
        self.assertEqual(replay.disposition, STREAM.GateOrderBookStreamDisposition.REPLAYED)
        self.assertEqual(replay.state.state_fingerprint, applied.state.state_fingerprint)

    def test_overlapping_delta_covering_expected_id_advances_once(self):
        first = STREAM.apply_gate_order_book_delta(
            STREAM.gate_order_book_stream_state_from_snapshot(snapshot(), subscription()), delta(99, 101),
        )
        second = STREAM.apply_gate_order_book_delta(first.state, delta(101, 103, event_id="ws:2", payload=2))
        self.assertEqual(second.disposition, STREAM.GateOrderBookStreamDisposition.APPLIED)
        self.assertEqual(second.state.next_update_id, 104)

    def test_gap_or_unverifiable_stale_delta_requires_reseed_without_advancing(self):
        state = STREAM.gate_order_book_stream_state_from_snapshot(snapshot(), subscription())
        gap = delta(103, 104, event_id="ws:gap", payload=3)
        result = STREAM.apply_gate_order_book_delta(state, gap)
        self.assertEqual(result.disposition, STREAM.GateOrderBookStreamDisposition.RESEED_REQUIRED)
        self.assertEqual(result.state.state_fingerprint, state.state_fingerprint)
        plan = STREAM.plan_gate_order_book_reseed(state, gap)
        self.assertEqual((plan.last_verified_update_id, plan.expected_next_update_id), (100, 101))
        self.assertEqual((plan.trigger_first_update_id, plan.trigger_last_update_id), (103, 104))
        stale = STREAM.apply_gate_order_book_delta(state, delta(90, 100, event_id="ws:stale", payload=4))
        self.assertEqual(stale.disposition, STREAM.GateOrderBookStreamDisposition.RESEED_REQUIRED)

    def test_reused_last_identity_with_changed_payload_is_typed_conflict(self):
        state = STREAM.gate_order_book_stream_state_from_snapshot(snapshot(), subscription())
        applied = STREAM.apply_gate_order_book_delta(state, delta(99, 101, event_id="ws:1", payload=1))
        conflict = STREAM.apply_gate_order_book_delta(applied.state, delta(99, 101, event_id="ws:1", payload=2))
        self.assertEqual(conflict.disposition, STREAM.GateOrderBookStreamDisposition.CONFLICT)
        self.assertEqual(conflict.state.state_fingerprint, applied.state.state_fingerprint)

    def test_scope_and_malformed_delta_facts_fail_closed(self):
        state = STREAM.gate_order_book_stream_state_from_snapshot(snapshot(), subscription())
        with self.assertRaises(STREAM.GateOrderBookStreamScopeConflict):
            STREAM.apply_gate_order_book_delta(state, delta(99, 101, instrument_id="ETH_USDT"))
        with self.assertRaises(STREAM.GateOrderBookStreamError):
            STREAM.GateOrderBookDelta(
                CAP.AssetMarketType.SPOT, "BTC_USDT", True, 1, (), (), UTC, UTC,
                "ws:bad", "gate-snapshot-1", "gate-rules-v1", fingerprint(5),
            )
        with self.assertRaises(STREAM.GateOrderBookStreamError):
            STREAM.GateOrderBookDelta(
                CAP.AssetMarketType.SPOT, "BTC_USDT", 1, 1, [], (), UTC, UTC,
                "ws:not-a-tuple", "gate-snapshot-1", "gate-rules-v1", fingerprint(6),
            )

    def test_empty_level_update_still_advances_authoritative_sequence(self):
        empty_update = STREAM.GateOrderBookDelta(
            market_type=CAP.AssetMarketType.PERPETUAL,
            instrument_id="BTC_USDT",
            first_update_id=99,
            last_update_id=101,
            bids=(),
            asks=(),
            occurred_at=UTC + timedelta(seconds=1),
            observed_at=UTC + timedelta(seconds=2),
            source_event_id="ws:empty-level-update",
            snapshot_id="gate-snapshot-1",
            rule_version="gate-rules-v1",
            payload_fingerprint=fingerprint(7),
        )
        perpetual_snapshot = MARKET.GateOrderBookSnapshot(
            market_type=CAP.AssetMarketType.PERPETUAL,
            instrument_id="BTC_USDT",
            bids=(MARKET.GateOrderBookLevel(Decimal("100"), Decimal("1")),),
            asks=(MARKET.GateOrderBookLevel(Decimal("101"), Decimal("1")),),
            occurred_at=UTC,
            observed_at=UTC,
            sequence=100,
            source_event_id="rest:perpetual:100",
            snapshot_id="gate-snapshot-1",
            rule_version="gate-rules-v1",
            evidence_hash=fingerprint(100),
            depth_limit=20,
        )
        result = STREAM.apply_gate_order_book_delta(
            STREAM.gate_order_book_stream_state_from_snapshot(
                perpetual_snapshot,
                subscription(market_type=CAP.AssetMarketType.PERPETUAL),
            ),
            empty_update,
        )
        self.assertEqual(result.disposition, STREAM.GateOrderBookStreamDisposition.APPLIED)
        self.assertEqual(result.state.next_update_id, 102)


if __name__ == "__main__":
    unittest.main()
