"""Offline fixtures for documented Gate order-book update notifications."""

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


CAP, MARKET, STREAM, PAYLOAD = load()


def subscription(*, market_type=None, depth_limit=100, update_interval="100ms"):
    return STREAM.GateOrderBookStreamSubscription(
        market_type=market_type or CAP.AssetMarketType.SPOT,
        instrument_id="BTC_USDT",
        snapshot_id="stream-snapshot-1",
        rule_version="gate-ws-rule-v1",
        depth_limit=depth_limit,
        update_interval=update_interval,
    )


def observed_at():
    return UTC + timedelta(seconds=5)


def spot_frame(*, full=True):
    result = {
        "t": "1780070400000",
        "l": "100",
        "s": "BTC_USDT",
        "U": 101,
        "u": 104,
        "b": [["99999.5", "0.0001"], ["99900", "0"]],
        "a": [["100000.5", "0.6135"]],
    }
    if full:
        result["full"] = True
    return {"channel": "spot.order_book_update", "event": "update", "error": None, "result": result}


def futures_frame(*, bids=None, asks=None):
    return {
        "channel": "futures.order_book_update",
        "event": "update",
        "error": None,
        "result": {
            "t": "1780070400000",
            "l": "100",
            "s": "BTC_USDT",
            "U": 101,
            "u": 104,
            "b": [{"p": "99999.5", "s": 0}, {"p": "99900", "s": "58794.5"}] if bids is None else bids,
            "a": [{"p": "100000.5", "s": "0"}] if asks is None else asks,
        },
    }


def snapshot(*, market_type, depth_limit=100):
    return MARKET.GateOrderBookSnapshot(
        market_type=market_type,
        instrument_id="BTC_USDT",
        bids=(MARKET.GateOrderBookLevel(Decimal("99999"), Decimal("1")),),
        asks=(MARKET.GateOrderBookLevel(Decimal("100001"), Decimal("1")),),
        occurred_at=UTC,
        observed_at=UTC,
        sequence=100,
        source_event_id="rest:100",
        snapshot_id="stream-snapshot-1",
        rule_version="gate-ws-rule-v1",
        evidence_hash="a" * 64,
        depth_limit=depth_limit,
    )


class GateOrderBookStreamPayloadContractTests(unittest.TestCase):
    def test_spot_official_shape_retains_full_depth_and_zero_deletion(self):
        frame = PAYLOAD.normalize_gate_order_book_update_frame(
            spot_frame(), subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws",
        )
        self.assertTrue(frame.is_full_snapshot)
        self.assertEqual(frame.channel, "spot.order_book_update")
        self.assertEqual((frame.delta.first_update_id, frame.delta.last_update_id), (101, 104))
        self.assertEqual(frame.delta.bids[1].quantity, Decimal("0"))
        self.assertEqual(frame.delta.source_event_id, "gate-ws:spot.order_book_update:BTC_USDT:101:104")

    def test_futures_object_levels_and_empty_book_updates_are_authoritative(self):
        parsed = PAYLOAD.normalize_gate_order_book_update_frame(
            futures_frame(),
            subscription=subscription(market_type=CAP.AssetMarketType.PERPETUAL),
            observed_at=observed_at(),
            source_event_prefix="gate-ws",
        )
        self.assertFalse(parsed.is_full_snapshot)
        self.assertEqual(parsed.delta.bids[0].quantity, Decimal("0"))
        self.assertEqual(parsed.delta.asks[0].quantity, Decimal("0"))
        empty = PAYLOAD.normalize_gate_order_book_update_frame(
            futures_frame(bids=[], asks=[]),
            subscription=subscription(market_type=CAP.AssetMarketType.PERPETUAL),
            observed_at=observed_at(),
            source_event_prefix="gate-ws",
        )
        state = STREAM.gate_order_book_stream_state_from_snapshot(
            snapshot(market_type=CAP.AssetMarketType.PERPETUAL),
            subscription(market_type=CAP.AssetMarketType.PERPETUAL),
        )
        result = STREAM.apply_gate_order_book_stream_frame(state, empty)
        self.assertEqual(result.disposition, STREAM.GateOrderBookStreamDisposition.APPLIED)
        self.assertEqual(result.state.next_update_id, 105)

    def test_authoritative_result_fingerprint_is_stable_but_detects_full_change(self):
        left = PAYLOAD.normalize_gate_order_book_update_frame(
            spot_frame(), subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws",
        )
        reordered = {
            "result": dict(reversed(list(spot_frame()["result"].items()))),
            "error": None,
            "event": "update",
            "channel": "spot.order_book_update",
        }
        right = PAYLOAD.normalize_gate_order_book_update_frame(
            reordered, subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws",
        )
        self.assertEqual(left.delta.payload_fingerprint, right.delta.payload_fingerprint)
        not_full = PAYLOAD.normalize_gate_order_book_update_frame(
            spot_frame(full=False), subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws",
        )
        self.assertNotEqual(left.delta.payload_fingerprint, not_full.delta.payload_fingerprint)

    def test_channel_event_scope_and_payload_shape_fail_closed(self):
        bad_channel = spot_frame(); bad_channel["channel"] = "futures.order_book_update"
        with self.assertRaises(PAYLOAD.GateOrderBookStreamPayloadError):
            PAYLOAD.normalize_gate_order_book_update_frame(bad_channel, subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws")
        ack = spot_frame(); ack["event"] = "subscribe"
        with self.assertRaises(PAYLOAD.GateOrderBookStreamPayloadError):
            PAYLOAD.normalize_gate_order_book_update_frame(ack, subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws")
        error_frame = spot_frame(); error_frame["error"] = {"code": 3, "message": "temporary"}
        with self.assertRaises(PAYLOAD.GateOrderBookStreamPayloadError):
            PAYLOAD.normalize_gate_order_book_update_frame(error_frame, subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws")
        non_bool_full = spot_frame(); non_bool_full["result"]["full"] = "true"
        with self.assertRaises(PAYLOAD.GateOrderBookStreamPayloadError):
            PAYLOAD.normalize_gate_order_book_update_frame(non_bool_full, subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws")
        wrong_depth = spot_frame(); wrong_depth["result"]["l"] = "20"
        with self.assertRaises(PAYLOAD.GateOrderBookStreamPayloadError):
            PAYLOAD.normalize_gate_order_book_update_frame(wrong_depth, subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws")
        float_level = spot_frame(); float_level["result"]["b"] = [[99999.5, "1"]]
        with self.assertRaises(PAYLOAD.GateOrderBookStreamPayloadError):
            PAYLOAD.normalize_gate_order_book_update_frame(float_level, subscription=subscription(), observed_at=observed_at(), source_event_prefix="gate-ws")
        wrong_shape = futures_frame(); wrong_shape["result"]["b"] = [["99999", "1"]]
        with self.assertRaises(PAYLOAD.GateOrderBookStreamPayloadError):
            PAYLOAD.normalize_gate_order_book_update_frame(
                wrong_shape,
                subscription=subscription(market_type=CAP.AssetMarketType.PERPETUAL),
                observed_at=observed_at(),
                source_event_prefix="gate-ws",
            )

    def test_rest_depth_scope_is_required_for_stream_anchor_and_frame_application(self):
        with self.assertRaises(STREAM.GateOrderBookStreamScopeConflict):
            STREAM.gate_order_book_stream_state_from_snapshot(
                snapshot(market_type=CAP.AssetMarketType.SPOT, depth_limit=100),
                subscription(depth_limit=20, update_interval="20ms"),
            )
        stream_subscription = subscription()
        state = STREAM.gate_order_book_stream_state_from_snapshot(
            snapshot(market_type=CAP.AssetMarketType.SPOT), stream_subscription,
        )
        wrong_subscription = subscription(depth_limit=20, update_interval="20ms")
        wrong_frame = PAYLOAD.normalize_gate_order_book_update_frame(
            {
                **spot_frame(),
                "result": {**spot_frame()["result"], "l": "20"},
            },
            subscription=wrong_subscription,
            observed_at=observed_at(),
            source_event_prefix="gate-ws",
        )
        with self.assertRaises(STREAM.GateOrderBookStreamScopeConflict):
            STREAM.apply_gate_order_book_stream_frame(state, wrong_frame)

    def test_full_depth_frame_can_reanchor_a_gapped_stream_but_incremental_frame_cannot(self):
        stream_subscription = subscription()
        state = STREAM.gate_order_book_stream_state_from_snapshot(
            snapshot(market_type=CAP.AssetMarketType.SPOT), stream_subscription,
        )
        gapped_full_payload = spot_frame()
        gapped_full_payload["result"]["U"] = 105
        gapped_full_payload["result"]["u"] = 108
        full_frame = PAYLOAD.normalize_gate_order_book_update_frame(
            gapped_full_payload,
            subscription=stream_subscription,
            observed_at=observed_at(),
            source_event_prefix="gate-ws",
        )
        reanchored = STREAM.apply_gate_order_book_stream_frame(state, full_frame)
        self.assertEqual(reanchored.disposition, STREAM.GateOrderBookStreamDisposition.APPLIED)
        self.assertEqual(reanchored.reason, "full_depth_snapshot_reanchors_stream")
        self.assertEqual(reanchored.state.next_update_id, 109)
        gapped_incremental_payload = spot_frame(full=False)
        gapped_incremental_payload["result"]["U"] = 105
        gapped_incremental_payload["result"]["u"] = 108
        incremental_frame = PAYLOAD.normalize_gate_order_book_update_frame(
            gapped_incremental_payload,
            subscription=stream_subscription,
            observed_at=observed_at(),
            source_event_prefix="gate-ws",
        )
        reseed = STREAM.apply_gate_order_book_stream_frame(state, incremental_frame)
        self.assertEqual(reseed.disposition, STREAM.GateOrderBookStreamDisposition.RESEED_REQUIRED)


if __name__ == "__main__":
    unittest.main()
