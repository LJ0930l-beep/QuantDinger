"""Offline contract tests for coherent Gate candle and order-book evidence."""

import ast
import importlib.util
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def load():
    names = (
        "app",
        "app.domain",
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts",
        "app.domain.market_data_quality_contracts",
        "app.domain.deterministic_backtest_contracts",
        "app.domain.backtest_dataset_contracts",
        "app.domain.gate_backtest_dataset_contracts",
        "app.domain.gate_order_book_stream_contracts",
        "app.domain.gate_order_book_materialization_contracts",
        "app.domain.gate_order_book_stream_session_contracts",
        "app.domain.gate_market_evidence_coherence_contracts",
    )
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        paths = {
            names[2]: ROOT / "app/domain/multi_asset_capability_contracts.py",
            names[3]: ROOT / "app/domain/gate_market_read_contracts.py",
            names[4]: ROOT / "app/domain/market_data_quality_contracts.py",
            names[5]: ROOT / "app/domain/deterministic_backtest_contracts.py",
            names[6]: ROOT / "app/domain/backtest_dataset_contracts.py",
            names[7]: ROOT / "app/domain/gate_backtest_dataset_contracts.py",
            names[8]: ROOT / "app/domain/gate_order_book_stream_contracts.py",
            names[9]: ROOT / "app/domain/gate_order_book_materialization_contracts.py",
            names[10]: ROOT / "app/domain/gate_order_book_stream_session_contracts.py",
            names[11]: ROOT / "app/domain/gate_market_evidence_coherence_contracts.py",
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


CAP, MARKET, QUALITY, BT, DATASET, GATE_DATASET, STREAM, MATERIAL, SESSION, COHERENCE = load()


class GateMarketEvidenceBundle:
    """Structural stand-in for the service-owned immutable bundle.

    The domain boundary intentionally does not import service code.  This
    fixture therefore exposes the exact validated public shape it consumes.
    Dedicated service tests cover construction of the actual bundle.
    """

    def __init__(self, market_type, instrument_id, interval, candles, order_book, observed_at, snapshot_id, rule_version, bundle_fingerprint):
        self.market_type = market_type
        self.instrument_id = instrument_id
        self.interval = interval
        self.candles = candles
        self.order_book = order_book
        self.observed_at = observed_at
        self.snapshot_id = snapshot_id
        self.rule_version = rule_version
        self.bundle_fingerprint = bundle_fingerprint


GateMarketEvidenceBundle.__module__ = "app.services.gate_market_research_service"


def candle(*, market_type=None, instrument_id="BTC_USDT", sequence=1, opened=None, observed=None, snapshot_id="coherence-snapshot-1", rule_version="coherence-rule-v1"):
    opened = UTC - timedelta(minutes=2) if opened is None else opened
    observed = UTC - timedelta(seconds=3) if observed is None else observed
    return MARKET.GateCandleFact(
        market_type=CAP.AssetMarketType.SPOT if market_type is None else market_type,
        instrument_id=instrument_id,
        interval="1m",
        open_time=opened,
        close_time=opened + timedelta(minutes=1),
        open_price=Decimal("100"), high_price=Decimal("102"), low_price=Decimal("99"),
        close_price=Decimal("101"), volume=Decimal("2"), occurred_at=opened,
        observed_at=observed, sequence=sequence, source_event_id=f"candle:{sequence}:{opened.isoformat()}",
        snapshot_id=snapshot_id, rule_version=rule_version, evidence_hash=("a" if sequence == 1 else "b") * 64,
    )


def snapshot(*, market_type=None, instrument_id="BTC_USDT", observed=None, snapshot_id="coherence-snapshot-1", rule_version="coherence-rule-v1", sequence=100):
    observed = UTC - timedelta(seconds=2) if observed is None else observed
    return MARKET.GateOrderBookSnapshot(
        market_type=CAP.AssetMarketType.SPOT if market_type is None else market_type,
        instrument_id=instrument_id,
        bids=(MARKET.GateOrderBookLevel(Decimal("100"), Decimal("1")),),
        asks=(MARKET.GateOrderBookLevel(Decimal("101"), Decimal("1")),),
        occurred_at=observed, observed_at=observed, sequence=sequence,
        source_event_id=f"book:{sequence}", snapshot_id=snapshot_id, rule_version=rule_version,
        evidence_hash="c" * 64, depth_limit=20,
    )


def stream_session(*, market_type=None, instrument_id="BTC_USDT", book=None, checked_at=UTC, snapshot_id="coherence-snapshot-1", rule_version="coherence-rule-v1"):
    market_type = CAP.AssetMarketType.SPOT if market_type is None else market_type
    book = snapshot(
        market_type=market_type, instrument_id=instrument_id, snapshot_id=snapshot_id,
        rule_version=rule_version,
    ) if book is None else book
    subject = STREAM.GateOrderBookStreamSubscription(
        market_type=market_type,
        instrument_id=instrument_id,
        snapshot_id=book.snapshot_id,
        rule_version=book.rule_version,
        depth_limit=20,
        update_interval="20ms",
    )
    return SESSION.gate_order_book_stream_session_from_snapshot(
        book, subject, as_of=checked_at, max_staleness=timedelta(hours=1),
    )


def bundle(*, market_type=None, instrument_id="BTC_USDT", candles=None, order_book=None, observed_at=UTC - timedelta(seconds=1), snapshot_id="coherence-snapshot-1", rule_version="coherence-rule-v1"):
    market_type = CAP.AssetMarketType.SPOT if market_type is None else market_type
    values = (candle(market_type=market_type, instrument_id=instrument_id, snapshot_id=snapshot_id, rule_version=rule_version),) if candles is None else candles
    book = snapshot(market_type=market_type, instrument_id=instrument_id, snapshot_id=snapshot_id, rule_version=rule_version) if order_book is None else order_book
    return GateMarketEvidenceBundle(
        market_type, instrument_id, "1m", values, book, observed_at, snapshot_id, rule_version, "d" * 64,
    )


def policy(**changes):
    values = {
        "policy_version": "coherence-v1",
        "max_candle_observation_age": timedelta(seconds=10),
        "max_candle_close_age": timedelta(minutes=2),
        "max_order_book_age": timedelta(seconds=10),
        "max_cross_feed_skew": timedelta(seconds=10),
    }
    values.update(changes)
    return COHERENCE.GateMarketEvidenceCoherencePolicy(**values)


class GateMarketEvidenceCoherenceContractTests(unittest.TestCase):
    def test_spot_and_perpetual_complete_evidence_produce_stable_ready_receipts(self):
        for market_type in (CAP.AssetMarketType.SPOT, CAP.AssetMarketType.PERPETUAL):
            value = bundle(market_type=market_type)
            session = stream_session(market_type=market_type)
            first = COHERENCE.assess_gate_market_evidence_coherence(value, session, as_of=UTC, policy=policy())
            second = COHERENCE.assess_gate_market_evidence_coherence(value, session, as_of=UTC, policy=policy())
            self.assertEqual(first.receipt_fingerprint, second.receipt_fingerprint)
            self.assertEqual(first.dataset.market_type, market_type.value)
            self.assertEqual(first.dataset.quality.status.value, "complete")
            self.assertEqual(first.latest_candle_close_at, UTC - timedelta(minutes=1))
            self.assertEqual(first.order_book_observed_at, UTC - timedelta(seconds=2))

    def test_rejects_stale_reseed_required_and_cross_scope_sessions(self):
        value = bundle()
        stale = stream_session(book=snapshot(observed=UTC - timedelta(seconds=20)), checked_at=UTC - timedelta(seconds=20))
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(value, stale, as_of=UTC, policy=policy())

        initial = stream_session(
            book=snapshot(observed=UTC - timedelta(seconds=4)),
            checked_at=UTC - timedelta(seconds=3),
        )
        subject = initial.materialized_state.subscription
        gap = STREAM.GateOrderBookDelta(
            market_type=CAP.AssetMarketType.SPOT, instrument_id="BTC_USDT", first_update_id=102, last_update_id=102,
            bids=(), asks=(), occurred_at=UTC - timedelta(seconds=2), observed_at=UTC - timedelta(seconds=2),
            source_event_id="gap:102", snapshot_id=subject.snapshot_id, rule_version=subject.rule_version,
            payload_fingerprint="e" * 64,
        )
        frame = STREAM.GateOrderBookStreamFrame(subject, gap, "spot.order_book_update", False)
        reseed_required = SESSION.apply_gate_order_book_stream_session_frame(
            initial, frame, as_of=UTC - timedelta(seconds=1), max_staleness=timedelta(seconds=10),
        ).session
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(value, reseed_required, as_of=UTC, policy=policy())

        foreign = stream_session(instrument_id="ETH_USDT", book=snapshot(instrument_id="ETH_USDT"))
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(value, foreign, as_of=UTC, policy=policy())

    def test_rejects_snapshot_rule_market_and_candle_quality_mismatches(self):
        current = stream_session()
        cases = (
            bundle(snapshot_id="different-snapshot"),
            bundle(rule_version="different-rule"),
            bundle(market_type=CAP.AssetMarketType.PERPETUAL),
            bundle(instrument_id="ETH_USDT"),
        )
        for value in cases:
            with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
                COHERENCE.assess_gate_market_evidence_coherence(value, current, as_of=UTC, policy=policy())

        first = candle(sequence=1, opened=UTC - timedelta(minutes=3))
        duplicate_sequence = candle(sequence=1, opened=UTC - timedelta(minutes=2))
        malformed = bundle(candles=(first, duplicate_sequence))
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(malformed, current, as_of=UTC, policy=policy())

    def test_rejects_observation_age_close_age_skew_and_non_utc_or_untyped_policy(self):
        current = stream_session()
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(
                bundle(observed_at=UTC - timedelta(seconds=20)), current, as_of=UTC, policy=policy(),
            )
        old_candle = candle(opened=UTC - timedelta(minutes=5))
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(
                bundle(candles=(old_candle,)), current, as_of=UTC, policy=policy(),
            )
        old_book = snapshot(observed=UTC - timedelta(seconds=8))
        skewed = stream_session(book=old_book, checked_at=UTC - timedelta(seconds=8))
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(
                bundle(), skewed, as_of=UTC, policy=policy(max_cross_feed_skew=timedelta(seconds=2)),
            )
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.assess_gate_market_evidence_coherence(
                bundle(), current, as_of=datetime(2026, 6, 1, 20, tzinfo=timezone(timedelta(hours=8))), policy=policy(),
            )
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            COHERENCE.GateMarketEvidenceCoherencePolicy("coherence-v1", 1, timedelta(seconds=1), timedelta(seconds=1), timedelta(seconds=1))

    def test_receipt_cannot_be_tampered_after_assessment(self):
        receipt = COHERENCE.assess_gate_market_evidence_coherence(bundle(), stream_session(), as_of=UTC, policy=policy())
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            replace(receipt, latest_candle_close_at=UTC)
        with self.assertRaises(COHERENCE.GateMarketEvidenceCoherenceError):
            replace(receipt, dataset=receipt.dataset.__class__(
                receipt.dataset.dataset_snapshot_id, receipt.dataset.venue, receipt.dataset.market_type,
                receipt.dataset.instrument_id, "other-rule", receipt.dataset.bars, receipt.dataset.quality,
                receipt.dataset.as_of, receipt.dataset.timeframe,
            ))

    def test_contract_has_no_transport_or_persistence_imports(self):
        source = (ROOT / "app/domain/gate_market_evidence_coherence_contracts.py").read_text(encoding="utf-8")
        imported = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        forbidden = ("requests", "websocket", "socket", "sql", "psycopg", "flask", "executor", "worker")
        self.assertFalse(any(item.startswith(forbidden) for item in imported))
        self.assertNotIn("commit(", source)
        self.assertNotIn("rollback(", source)


if __name__ == "__main__":
    unittest.main()
