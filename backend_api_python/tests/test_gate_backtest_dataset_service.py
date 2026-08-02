from __future__ import annotations

import sys
import types
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
app_module = types.ModuleType("app")
app_module.__path__ = [str(ROOT / "app")]
domain_module = types.ModuleType("app.domain")
domain_module.__path__ = [str(ROOT / "app" / "domain")]
services_module = types.ModuleType("app.services")
services_module.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app", app_module)
sys.modules.setdefault("app.domain", domain_module)
sys.modules.setdefault("app.services", services_module)

from app.domain.gate_market_read_contracts import GateCandleFact, GateMarketKind
from app.domain.multi_asset_capability_contracts import AssetMarketType
from app.domain.gate_market_read_contracts import GateOrderBookLevel, GateOrderBookSnapshot
from app.services.gate_market_research_service import GateMarketEvidenceBundle
from app.services.gate_backtest_dataset_service import GateBacktestDatasetError, GateBacktestDatasetService


UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _evidence(*, observed_at: datetime = T0 + timedelta(minutes=5), duplicate: bool = False):
    candles = []
    for index in range(4):
        opened = T0 + timedelta(minutes=index)
        candles.append(GateCandleFact(
            market_type=AssetMarketType.SPOT,
            instrument_id="BTC_USDT",
            interval="1m",
            open_time=opened,
            close_time=opened + timedelta(minutes=1),
            open_price=Decimal(100 + index),
            high_price=Decimal(101 + index),
            low_price=Decimal(99 + index),
            close_price=Decimal("100.5") + index,
            volume=Decimal(2),
            occurred_at=opened + timedelta(minutes=1),
            observed_at=observed_at,
            sequence=index,
            source_event_id="candle-0" if duplicate and index == 3 else f"candle-{index}",
            snapshot_id="snap-1",
            rule_version="rules-v1",
            evidence_hash=f"hash-{index}",
        ))
    book = GateOrderBookSnapshot(
        market_type=AssetMarketType.SPOT,
        instrument_id="BTC_USDT",
        bids=(GateOrderBookLevel(Decimal(100), Decimal(1)),),
        asks=(GateOrderBookLevel(Decimal(101), Decimal(1)),),
        occurred_at=T0 + timedelta(minutes=3),
        observed_at=observed_at,
        sequence=3,
        source_event_id="book-3",
        snapshot_id="snap-1",
        rule_version="rules-v1",
        evidence_hash="book-hash",
    )
    return GateMarketEvidenceBundle(
        AssetMarketType.SPOT, "BTC_USDT", "1m", tuple(candles), book,
        observed_at, "snap-1", "rules-v1",
    )


class GateBacktestDatasetServiceTests(unittest.TestCase):
    def test_builds_complete_point_in_time_dataset(self):
        dataset = GateBacktestDatasetService().build(_evidence())
        self.assertEqual(dataset.venue, "gate")
        self.assertEqual(dataset.market_type, "spot")
        self.assertEqual(dataset.instrument_id, "BTC_USDT")
        self.assertEqual(len(dataset.bars), 4)
        self.assertEqual(dataset.quality.status.value, "complete")
        self.assertEqual(dataset.quality.accepted_events[0].source, "gate.candle.1m")

    def test_rejects_mixed_interval_evidence(self):
        evidence = _evidence()
        mixed = replace(evidence, candles=(evidence.candles[0], replace(evidence.candles[1], interval="5m"), *evidence.candles[2:]))
        with self.assertRaises(GateBacktestDatasetError):
            GateBacktestDatasetService().build(mixed)

    def test_rejects_untyped_evidence(self):
        with self.assertRaises(GateBacktestDatasetError):
            GateBacktestDatasetService().build(None)


if __name__ == "__main__":
    unittest.main()
