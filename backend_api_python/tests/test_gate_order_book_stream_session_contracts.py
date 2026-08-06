"""Offline recovery and health tests for public Gate depth sessions."""

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 5, 29, 16, 0, tzinfo=timezone.utc)
WINDOW = timedelta(seconds=30)


def load():
    names = (
        "app",
        "app.domain",
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts",
        "app.domain.gate_order_book_stream_contracts",
        "app.domain.gate_order_book_stream_payload_contracts",
        "app.domain.gate_order_book_materialization_contracts",
        "app.domain.gate_order_book_stream_session_contracts",
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
            names[7]: ROOT / "app/domain/gate_order_book_stream_session_contracts.py",
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


CAP, MARKET, STREAM, PAYLOAD, MATERIAL, SESSION = load()


def subscription(*, snapshot_id="session-snapshot-1", market_type=None):
    return STREAM.GateOrderBookStreamSubscription(
        market_type=market_type or CAP.AssetMarketType.SPOT,
        instrument_id="BTC_USDT",
        snapshot_id=snapshot_id,
        rule_version="gate-ws-rule-v1",
        depth_limit=20,
        update_interval="20ms",
    )


def snapshot(*, snapshot_id="session-snapshot-1", sequence=100, observed=UTC, market_type=None):
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
        "b": [["100", "2"], ["99", "0"]] if bids is None else bids,
        "a": [["101", "0"], ["100.5", "3"]] if asks is None else asks,
    }
    if full:
        result["full"] = True
    return {"channel": "spot.order_book_update", "event": "update", "error": None, "result": result}


def frame(raw, *, subject):
    return PAYLOAD.normalize_gate_order_book_update_frame(
        raw,
        subscription=subject,
        observed_at=UTC + timedelta(seconds=5),
        source_event_prefix="gate-ws",
    )


def session(*, subject=None, snap=None, as_of=UTC):
    subject = subject or subscription()
    snap = snap or snapshot(snapshot_id=subject.snapshot_id)
    return SESSION.gate_order_book_stream_session_from_snapshot(
        snap,
        subject,
        as_of=as_of,
        max_staleness=WINDOW,
    )


class GateOrderBookStreamSessionContractTests(unittest.TestCase):
    def test_first_valid_frame_becomes_healthy_local_depth_and_exact_replay_is_immutable(self):
        subject = subscription()
        initial = session(subject=subject)
        update = frame(payload(), subject=subject)
        applied = SESSION.apply_gate_order_book_stream_session_frame(
            initial, update, as_of=UTC + timedelta(seconds=6), max_staleness=WINDOW,
        )
        self.assertEqual(applied.disposition, SESSION.GateOrderBookStreamSessionDisposition.APPLIED)
        self.assertEqual(applied.session.health, SESSION.GateOrderBookEvidenceHealth.HEALTHY)
        self.assertEqual(applied.session.healthy_snapshot().sequence, 101)
        self.assertEqual([(item.price, item.quantity) for item in applied.session.healthy_snapshot().bids], [(Decimal("100"), Decimal("2"))])
        replay = SESSION.apply_gate_order_book_stream_session_frame(
            applied.session, update, as_of=UTC + timedelta(seconds=7), max_staleness=WINDOW,
        )
        self.assertEqual(replay.disposition, SESSION.GateOrderBookStreamSessionDisposition.REPLAYED)
        self.assertEqual(replay.session.session_fingerprint, applied.session.session_fingerprint)
        self.assertEqual(replay.session.healthy_snapshot(), applied.session.healthy_snapshot())

    def test_gap_makes_prior_depth_unusable_until_a_fresher_same_market_rest_reseed(self):
        subject = subscription()
        initial = session(subject=subject)
        gapped = frame(payload(first=103, last=104), subject=subject)
        recovery = SESSION.apply_gate_order_book_stream_session_frame(
            initial, gapped, as_of=UTC + timedelta(seconds=6), max_staleness=WINDOW,
        )
        self.assertEqual(recovery.disposition, SESSION.GateOrderBookStreamSessionDisposition.RESEED_REQUIRED)
        self.assertEqual(recovery.session.health, SESSION.GateOrderBookEvidenceHealth.RESEED_REQUIRED)
        self.assertEqual(recovery.session.reseed_plan.expected_next_update_id, 101)
        with self.assertRaises(SESSION.GateOrderBookStreamSessionError):
            recovery.session.healthy_snapshot()
        blocked = SESSION.apply_gate_order_book_stream_session_frame(
            recovery.session, frame(payload(), subject=subject), as_of=UTC + timedelta(seconds=7), max_staleness=WINDOW,
        )
        self.assertEqual(blocked.disposition, SESSION.GateOrderBookStreamSessionDisposition.RESEED_REQUIRED)
        reseed_subject = subscription(snapshot_id="session-snapshot-2")
        reseed_snapshot = snapshot(
            snapshot_id="session-snapshot-2",
            sequence=104,
            observed=UTC + timedelta(seconds=8),
        )
        reseeded = SESSION.reseed_gate_order_book_stream_session(
            recovery.session,
            reseed_snapshot,
            reseed_subject,
            as_of=UTC + timedelta(seconds=9),
            max_staleness=WINDOW,
        )
        self.assertEqual(reseeded.disposition, SESSION.GateOrderBookStreamSessionDisposition.RESEEDED)
        self.assertEqual(reseeded.session.health, SESSION.GateOrderBookEvidenceHealth.HEALTHY)
        after_reseed = frame(payload(first=105, last=105), subject=reseed_subject)
        applied = SESSION.apply_gate_order_book_stream_session_frame(
            reseeded.session, after_reseed, as_of=UTC + timedelta(seconds=10), max_staleness=WINDOW,
        )
        self.assertEqual(applied.disposition, SESSION.GateOrderBookStreamSessionDisposition.APPLIED)
        self.assertEqual(applied.session.healthy_snapshot().sequence, 105)

    def test_stale_evidence_is_not_consumable_and_requires_a_fresh_rest_snapshot(self):
        initial = session()
        stale = SESSION.assess_gate_order_book_stream_session_freshness(
            initial,
            as_of=UTC + timedelta(seconds=31),
            max_staleness=WINDOW,
        )
        self.assertEqual(stale.disposition, SESSION.GateOrderBookStreamSessionDisposition.STALE)
        self.assertEqual(stale.session.health, SESSION.GateOrderBookEvidenceHealth.STALE)
        with self.assertRaises(SESSION.GateOrderBookStreamSessionError):
            stale.session.healthy_snapshot()
        reseeded = SESSION.reseed_gate_order_book_stream_session(
            stale.session,
            snapshot(snapshot_id="session-snapshot-2", sequence=100, observed=UTC + timedelta(seconds=32)),
            subscription(snapshot_id="session-snapshot-2"),
            as_of=UTC + timedelta(seconds=33),
            max_staleness=WINDOW,
        )
        self.assertEqual(reseeded.disposition, SESSION.GateOrderBookStreamSessionDisposition.RESEEDED)

    def test_staleness_boundary_is_inclusive_and_a_stale_session_cannot_apply_later_frames(self):
        initial = session()
        boundary = SESSION.assess_gate_order_book_stream_session_freshness(
            initial,
            as_of=UTC + timedelta(seconds=30),
            max_staleness=WINDOW,
        )
        self.assertEqual(boundary.disposition, SESSION.GateOrderBookStreamSessionDisposition.REPLAYED)
        stale = SESSION.assess_gate_order_book_stream_session_freshness(
            initial,
            as_of=UTC + timedelta(seconds=31),
            max_staleness=WINDOW,
        ).session
        blocked = SESSION.apply_gate_order_book_stream_session_frame(
            stale,
            frame(payload(), subject=subscription()),
            as_of=UTC + timedelta(seconds=32),
            max_staleness=WINDOW,
        )
        self.assertEqual(blocked.disposition, SESSION.GateOrderBookStreamSessionDisposition.STALE)
        self.assertEqual(blocked.session.materialized_state.materialization_fingerprint, initial.materialized_state.materialization_fingerprint)

    def test_scope_and_sequence_rollback_attempts_fail_closed(self):
        initial = session()
        stale = SESSION.assess_gate_order_book_stream_session_freshness(
            initial,
            as_of=UTC + timedelta(seconds=31),
            max_staleness=WINDOW,
        ).session
        with self.assertRaises(SESSION.GateOrderBookStreamSessionError):
            SESSION.reseed_gate_order_book_stream_session(
                stale,
                snapshot(snapshot_id="session-snapshot-2", sequence=99, observed=UTC + timedelta(seconds=32)),
                subscription(snapshot_id="session-snapshot-2"),
                as_of=UTC + timedelta(seconds=33),
                max_staleness=WINDOW,
            )
        with self.assertRaises(SESSION.GateOrderBookStreamSessionError):
            SESSION.reseed_gate_order_book_stream_session(
                stale,
                snapshot(snapshot_id="session-snapshot-2", observed=UTC + timedelta(seconds=32)),
                STREAM.GateOrderBookStreamSubscription(
                    market_type=CAP.AssetMarketType.SPOT,
                    instrument_id="BTC_USDT",
                    snapshot_id="session-snapshot-2",
                    rule_version="different-rule-v1",
                    depth_limit=20,
                    update_interval="20ms",
                ),
                as_of=UTC + timedelta(seconds=33),
                max_staleness=WINDOW,
            )
        with self.assertRaises(SESSION.GateOrderBookStreamSessionError):
            SESSION.reseed_gate_order_book_stream_session(
                stale,
                snapshot(snapshot_id="session-snapshot-2", observed=UTC + timedelta(seconds=32), market_type=CAP.AssetMarketType.PERPETUAL),
                subscription(snapshot_id="session-snapshot-2", market_type=CAP.AssetMarketType.PERPETUAL),
                as_of=UTC + timedelta(seconds=33),
                max_staleness=WINDOW,
            )

    def test_full_frame_can_reanchor_while_healthy_but_not_after_a_detected_gap(self):
        subject = subscription()
        initial = session(subject=subject)
        full = frame(
            payload(first=105, last=108, full=True, bids=[["100.25", "4"]], asks=[["100.75", "5"]]),
            subject=subject,
        )
        reanchored = SESSION.apply_gate_order_book_stream_session_frame(
            initial, full, as_of=UTC + timedelta(seconds=6), max_staleness=WINDOW,
        )
        self.assertEqual(reanchored.disposition, SESSION.GateOrderBookStreamSessionDisposition.APPLIED)
        self.assertEqual(reanchored.reason, "full_depth_snapshot_reanchors_stream")
        failed = SESSION.apply_gate_order_book_stream_session_frame(
            initial, frame(payload(first=103, last=104), subject=subject), as_of=UTC + timedelta(seconds=6), max_staleness=WINDOW,
        ).session
        blocked = SESSION.apply_gate_order_book_stream_session_frame(
            failed, full, as_of=UTC + timedelta(seconds=7), max_staleness=WINDOW,
        )
        self.assertEqual(blocked.disposition, SESSION.GateOrderBookStreamSessionDisposition.RESEED_REQUIRED)

    def test_pending_reseed_is_repeatably_reported_without_manufacturing_a_second_frame_result(self):
        subject = subscription()
        recovery = SESSION.apply_gate_order_book_stream_session_frame(
            session(subject=subject),
            frame(payload(first=103, last=104), subject=subject),
            as_of=UTC + timedelta(seconds=6),
            max_staleness=WINDOW,
        ).session
        refreshed = SESSION.assess_gate_order_book_stream_session_freshness(
            recovery,
            as_of=UTC + timedelta(seconds=7),
            max_staleness=WINDOW,
        )
        self.assertEqual(refreshed.disposition, SESSION.GateOrderBookStreamSessionDisposition.RESEED_REQUIRED)
        self.assertIsNone(refreshed.materialization_result)
        self.assertEqual(refreshed.session.session_fingerprint, recovery.session_fingerprint)

    def test_conflicting_reuse_of_a_stream_identity_remains_typed_and_requires_reseed(self):
        subject = subscription()
        initial = session(subject=subject)
        first = SESSION.apply_gate_order_book_stream_session_frame(
            initial, frame(payload(), subject=subject), as_of=UTC + timedelta(seconds=6), max_staleness=WINDOW,
        )
        conflicting = frame(
            payload(bids=[["100", "9"]], asks=[["101", "0"], ["100.5", "3"]]),
            subject=subject,
        )
        result = SESSION.apply_gate_order_book_stream_session_frame(
            first.session, conflicting, as_of=UTC + timedelta(seconds=7), max_staleness=WINDOW,
        )
        self.assertEqual(result.disposition, SESSION.GateOrderBookStreamSessionDisposition.CONFLICT)
        self.assertEqual(result.session.health, SESSION.GateOrderBookEvidenceHealth.RESEED_REQUIRED)
        self.assertEqual(result.session.materialized_state.materialization_fingerprint, first.session.materialized_state.materialization_fingerprint)

    def test_perpetual_empty_updates_and_public_contract_have_no_transport_or_order_capability(self):
        subject = subscription(market_type=CAP.AssetMarketType.PERPETUAL)
        initial = session(subject=subject, snap=snapshot(snapshot_id=subject.snapshot_id, market_type=CAP.AssetMarketType.PERPETUAL))
        raw = {
            "channel": "futures.order_book_update", "event": "update", "error": None,
            "result": {"t": "1780070400000", "l": "20", "s": "BTC_USDT", "U": 101, "u": 101, "b": [], "a": []},
        }
        applied = SESSION.apply_gate_order_book_stream_session_frame(
            initial, frame(raw, subject=subject), as_of=UTC + timedelta(seconds=6), max_staleness=WINDOW,
        )
        self.assertEqual(applied.disposition, SESSION.GateOrderBookStreamSessionDisposition.APPLIED)
        self.assertEqual(applied.session.healthy_snapshot().sequence, 101)
        source = (ROOT / "app/domain/gate_order_book_stream_session_contracts.py").read_text(encoding="utf-8")
        self.assertNotIn("requests", source)
        self.assertNotIn("websocket", source)
        self.assertNotIn("submit_order", source)
        self.assertNotIn("commit(", source)


if __name__ == "__main__":
    unittest.main()
