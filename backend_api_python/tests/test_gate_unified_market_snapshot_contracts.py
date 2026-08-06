from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.domain.gate_market_read_contracts import GateCandleFact, GateOrderBookLevel, GateOrderBookSnapshot
from app.domain.multi_asset_capability_contracts import AssetMarketType
from app.domain.gate_unified_market_snapshot_contracts import GateUnifiedMarketSnapshotError
from app.services.gate_market_research_service import GateMarketResearchServiceError
from app.domain.gate_unified_market_snapshot_contracts import (
    GateUnifiedMarketSnapshotError,
    build_gate_unified_market_snapshot,
)
from app.services.gate_market_research_service import GateMarketEvidenceBundle
from app.services.readonly_gate_unified_market_service import ReadonlyGateUnifiedMarketService


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def _bundle(market):
    # Other Gate tests load domain modules in isolated import sandboxes.  Use
    # the enum class captured by the fact constructor so this suite remains
    # deterministic regardless of collection order.
    market_enum = GateCandleFact.__post_init__.__globals__["AssetMarketType"](market.value)
    candle = GateCandleFact(
        market_type=market_enum, instrument_id="BTC_USDT", interval="1m",
        open_time=NOW - timedelta(minutes=2), close_time=NOW - timedelta(minutes=1),
        open_price=Decimal("100"), high_price=Decimal("102"), low_price=Decimal("99"),
        close_price=Decimal("101"), volume=Decimal("2"), occurred_at=NOW - timedelta(minutes=2),
        observed_at=NOW, sequence=1, source_event_id=f"fixture:{market.value}:1",
        snapshot_id=f"snap-{market.value}", rule_version="rules-v1", evidence_hash=f"hash-{market.value}-1",
    )
    book = GateOrderBookSnapshot(
        market_type=market_enum, instrument_id="BTC_USDT",
        bids=(GateOrderBookLevel(Decimal("100"), Decimal("1")),),
        asks=(GateOrderBookLevel(Decimal("101"), Decimal("1")),),
        occurred_at=NOW - timedelta(seconds=1), observed_at=NOW, sequence=2,
        source_event_id=f"fixture:{market.value}:2", snapshot_id=f"snap-{market.value}",
        rule_version="rules-v1", evidence_hash=f"hash-{market.value}-2",
    )
    return GateMarketEvidenceBundle(market_enum, "BTC_USDT", "1m", (candle,), book, NOW, f"snap-{market.value}", "rules-v1")


def test_unified_market_snapshot_is_separate_and_deterministic():
    first = build_gate_unified_market_snapshot(
        (_bundle(AssetMarketType.SPOT), _bundle(AssetMarketType.PERPETUAL)),
        instrument_id="BTC_USDT", interval="1m", observed_at=NOW,
    )
    second = build_gate_unified_market_snapshot(
        (_bundle(AssetMarketType.PERPETUAL), _bundle(AssetMarketType.SPOT)),
        instrument_id="BTC_USDT", interval="1m", observed_at=NOW,
    )
    assert first.snapshot_fingerprint == second.snapshot_fingerprint
    public = first.to_public_dict()
    assert set(public["markets"]) == {"spot", "perpetual"}
    assert public["live_enabled"] is False
    assert public["markets"]["spot"]["candles"][0]["close"] == "101"


def test_unified_market_snapshot_rejects_duplicate_or_mismatched_scope():
    with pytest.raises((GateUnifiedMarketSnapshotError, GateMarketResearchServiceError)):
        build_gate_unified_market_snapshot(
            (_bundle(AssetMarketType.SPOT), _bundle(AssetMarketType.SPOT)),
            instrument_id="BTC_USDT", interval="1m", observed_at=NOW,
        )
    with pytest.raises((GateUnifiedMarketSnapshotError, GateMarketResearchServiceError)):
        build_gate_unified_market_snapshot(
            (_bundle(AssetMarketType.SPOT),), instrument_id="ETH_USDT", interval="1m", observed_at=NOW,
        )


def test_unified_market_service_is_fail_closed_and_typed():
    value = build_gate_unified_market_snapshot(
        (_bundle(AssetMarketType.SPOT), _bundle(AssetMarketType.PERPETUAL)),
        instrument_id="BTC_USDT", interval="1m", observed_at=NOW,
    )
    status, body = ReadonlyGateUnifiedMarketService(lambda *args: value).read_response(
        instrument_id="BTC_USDT", observed_at=NOW,
    )
    assert status == 200
    assert body["status"] == "READY"
    assert body["live_enabled"] is False

    status, body = ReadonlyGateUnifiedMarketService().read_response(
        instrument_id="BTC_USDT", observed_at=NOW,
    )
    assert status == 503
    assert body["reason"] == "unified_market_read_disabled"


def test_unified_market_service_rejects_bad_scope_before_provider():
    called = []
    service = ReadonlyGateUnifiedMarketService(lambda *args: called.append(args))
    with pytest.raises(Exception):
        service.read_response(instrument_id="BTC USDT", observed_at=NOW)
    assert called == []
