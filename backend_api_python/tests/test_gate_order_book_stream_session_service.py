"""Offline tests for the transport-free Gate order-book session boundary."""

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
        "app.services",
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts",
        "app.domain.gate_order_book_stream_contracts",
        "app.domain.gate_order_book_stream_payload_contracts",
        "app.domain.gate_order_book_materialization_contracts",
        "app.domain.gate_order_book_stream_session_contracts",
        "app.services.gate_order_book_stream_session_service",
    )
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        paths = {
            names[3]: ROOT / "app/domain/multi_asset_capability_contracts.py",
            names[4]: ROOT / "app/domain/gate_market_read_contracts.py",
            names[5]: ROOT / "app/domain/gate_order_book_stream_contracts.py",
            names[6]: ROOT / "app/domain/gate_order_book_stream_payload_contracts.py",
            names[7]: ROOT / "app/domain/gate_order_book_materialization_contracts.py",
            names[8]: ROOT / "app/domain/gate_order_book_stream_session_contracts.py",
            names[9]: ROOT / "app/services/gate_order_book_stream_session_service.py",
        }
        for name in names[3:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return tuple(sys.modules[name] for name in names[3:])
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


CAP, MARKET, STREAM, PAYLOAD, MATERIAL, SESSION, SERVICE = load()


def snapshot(*, snapshot_id="service-snapshot-1", sequence=100, observed=UTC, market_type=None, instrument_id="BTC_USDT"):
    return MARKET.GateOrderBookSnapshot(
        market_type=CAP.AssetMarketType.SPOT if market_type is None else market_type,
        instrument_id=instrument_id,
        bids=(MARKET.GateOrderBookLevel(Decimal("100"), Decimal("1")),),
        asks=(MARKET.GateOrderBookLevel(Decimal("101"), Decimal("1")),),
        occurred_at=observed,
        observed_at=observed,
        sequence=sequence,
        source_event_id=f"rest:{sequence}",
        snapshot_id=snapshot_id,
        rule_version="gate-ws-rule-v1",
        evidence_hash=("a" if sequence == 100 else "b") * 64,
        depth_limit=20,
    )


def payload(*, first=101, last=101, full=False, bids=None, asks=None):
    result = {
        "t": "1780070400000",
        "l": "20",
        "s": "BTC_USDT",
        "U": first,
        "u": last,
        "b": [["100", "2"]] if bids is None else bids,
        "a": [["101", "0"], ["100.5", "3"]] if asks is None else asks,
    }
    if full:
        result["full"] = True
    return {"channel": "spot.order_book_update", "event": "update", "error": None, "result": result}


def service(**changes):
    values = {
        "market_type": CAP.AssetMarketType.SPOT,
        "instrument_id": "BTC_USDT",
        "rule_version": "gate-ws-rule-v1",
        "depth_limit": 20,
        "update_interval": "20ms",
        "source_event_prefix": "gate-public",
        "max_staleness": timedelta(seconds=30),
    }
    values.update(changes)
    return SERVICE.GateOrderBookStreamSessionService(**values)


class GateOrderBookStreamSessionServiceTests(unittest.TestCase):
    def test_owns_one_fixed_policy_but_binds_each_rest_snapshot_identity_explicitly(self):
        subject = service()
        initial = subject.start(snapshot(), as_of=UTC)
        self.assertEqual(initial.health, SESSION.GateOrderBookEvidenceHealth.HEALTHY)
        self.assertEqual(initial.materialized_state.subscription.snapshot_id, "service-snapshot-1")
        receipt = subject.receive_update(
            initial,
            payload(),
            observed_at=UTC + timedelta(seconds=5),
            as_of=UTC + timedelta(seconds=6),
        )
        self.assertEqual(receipt.result.disposition, SESSION.GateOrderBookStreamSessionDisposition.APPLIED)
        self.assertEqual(receipt.frame.delta.source_event_id, "gate-public:spot.order_book_update:BTC_USDT:101:101")
        self.assertEqual(receipt.result.session.healthy_snapshot().sequence, 101)
        replay = subject.receive_update(
            receipt.result.session,
            payload(),
            observed_at=UTC + timedelta(seconds=5),
            as_of=UTC + timedelta(seconds=7),
        )
        self.assertEqual(replay.result.disposition, SESSION.GateOrderBookStreamSessionDisposition.REPLAYED)
        self.assertEqual(replay.frame.frame_fingerprint, receipt.frame.frame_fingerprint)
        self.assertEqual(replay.result.session.session_fingerprint, receipt.result.session.session_fingerprint)
        # A receipt captures the actual typed outcome, so APPLIED and an exact
        # REPLAYED delivery intentionally have different audit fingerprints.
        self.assertNotEqual(replay.receipt_fingerprint, receipt.receipt_fingerprint)

    def test_receipt_cannot_pair_a_frame_with_a_different_materialization_result(self):
        subject = service()
        initial = subject.start(snapshot(), as_of=UTC)
        first = subject.receive_update(
            initial,
            payload(),
            observed_at=UTC + timedelta(seconds=5),
            as_of=UTC + timedelta(seconds=6),
        )
        second = subject.receive_update(
            first.result.session,
            payload(first=102, last=102),
            observed_at=UTC + timedelta(seconds=7),
            as_of=UTC + timedelta(seconds=8),
        )
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            SERVICE.GateOrderBookStreamSessionReceipt(second.frame, first.result)

    def test_bad_payload_is_typed_and_does_not_mutate_or_smuggle_a_transport_error(self):
        subject = service()
        initial = subject.start(snapshot(), as_of=UTC)
        raw = payload(); raw["error"] = {"message": "opaque-network-detail"}
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError) as caught:
            subject.receive_update(
                initial,
                raw,
                observed_at=UTC + timedelta(seconds=5),
                as_of=UTC + timedelta(seconds=6),
            )
        self.assertNotIn("opaque-network-detail", str(caught.exception))
        self.assertEqual(initial.healthy_snapshot().sequence, 100)

    def test_reseed_only_accepts_a_fresh_snapshot_with_the_fixed_policy_scope(self):
        subject = service()
        initial = subject.start(snapshot(), as_of=UTC)
        gapped = subject.receive_update(
            initial,
            payload(first=103, last=104),
            observed_at=UTC + timedelta(seconds=5),
            as_of=UTC + timedelta(seconds=6),
        ).result.session
        self.assertEqual(gapped.health, SESSION.GateOrderBookEvidenceHealth.RESEED_REQUIRED)
        reseeded = subject.reseed(
            gapped,
            snapshot(snapshot_id="service-snapshot-2", sequence=104, observed=UTC + timedelta(seconds=7)),
            as_of=UTC + timedelta(seconds=8),
        )
        self.assertEqual(reseeded.disposition, SESSION.GateOrderBookStreamSessionDisposition.RESEEDED)
        self.assertEqual(reseeded.session.materialized_state.subscription.snapshot_id, "service-snapshot-2")
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            subject.reseed(
                gapped,
                MARKET.GateOrderBookSnapshot(
                    market_type=CAP.AssetMarketType.SPOT,
                    instrument_id="ETH_USDT",
                    bids=(MARKET.GateOrderBookLevel(Decimal("10"), Decimal("1")),),
                    asks=(MARKET.GateOrderBookLevel(Decimal("11"), Decimal("1")),),
                    occurred_at=UTC + timedelta(seconds=7),
                    observed_at=UTC + timedelta(seconds=7),
                    sequence=104,
                    source_event_id="rest:104",
                    snapshot_id="wrong-scope",
                    rule_version="gate-ws-rule-v1",
                    evidence_hash="b" * 64,
                    depth_limit=20,
                ),
                as_of=UTC + timedelta(seconds=8),
            )

    def test_assess_and_reseed_reject_a_session_from_another_fixed_policy(self):
        subject = service()
        foreign = service(instrument_id="ETH_USDT").start(
            snapshot(instrument_id="ETH_USDT"),
            as_of=UTC,
        )
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            subject.assess(foreign, as_of=UTC + timedelta(seconds=1))
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            subject.reseed(
                foreign,
                snapshot(snapshot_id="service-snapshot-2", sequence=101, observed=UTC + timedelta(seconds=1)),
                as_of=UTC + timedelta(seconds=2),
            )

    def test_all_public_entrypoints_map_domain_validation_failures_to_service_errors(self):
        subject = service()
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            subject.start(snapshot(), as_of=UTC - timedelta(seconds=1))
        initial = subject.start(snapshot(), as_of=UTC)
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            subject.assess(initial, as_of=datetime(2026, 5, 29, 16, 0))
        stale = subject.assess(initial, as_of=UTC + timedelta(seconds=31)).session
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            subject.reseed(stale, snapshot(snapshot_id="service-snapshot-2", sequence=100, observed=UTC + timedelta(seconds=32)), as_of=datetime(2026, 5, 29, 16, 1))

    def test_policy_rejects_unsupported_depth_and_source_module_has_no_io_or_trade_capability(self):
        with self.assertRaises(SERVICE.GateOrderBookStreamSessionServiceError):
            service(depth_limit=30)
        source = (ROOT / "app/services/gate_order_book_stream_session_service.py").read_text(encoding="utf-8")
        for forbidden in ("urlopen", "requests", "websocket", "connect(", "submit_order", "place_order", "cancel_order", "commit(", "rollback("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
