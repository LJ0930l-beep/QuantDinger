import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    names = [
        "app",
        "app.domain",
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts",
        "app.domain.market_data_quality_contracts",
        "app.domain.deterministic_backtest_contracts",
        "app.domain.backtest_dataset_contracts",
        "app.domain.gate_backtest_dataset_contracts",
    ]
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
        }
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[7]], sys.modules[names[2]], sys.modules[names[3]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, CAP, MARKET = load()


def candle(**changes):
    facts = dict(
        market_type=CAP.AssetMarketType.SPOT,
        instrument_id="BTC_USDT",
        interval="1m",
        open_time=UTC + timedelta(minutes=1),
        close_time=UTC + timedelta(minutes=2),
        open_price=Decimal("100"), high_price=Decimal("102"),
        low_price=Decimal("99"), close_price=Decimal("101"),
        volume=Decimal("10"), occurred_at=UTC + timedelta(minutes=1),
        observed_at=UTC + timedelta(minutes=2), sequence=1,
        source_event_id="event-1", snapshot_id="dataset-1",
        rule_version="gate-rules-v1", evidence_hash="hash-1",
    )
    facts.update(changes)
    return MARKET.GateCandleFact(**facts)


class GateBacktestDatasetTests(unittest.TestCase):
    def test_builds_complete_point_in_time_dataset(self):
        result = M.build_gate_backtest_dataset(
            (candle(), candle(
                open_time=UTC + timedelta(minutes=3), close_time=UTC + timedelta(minutes=4),
                occurred_at=UTC + timedelta(minutes=3), observed_at=UTC + timedelta(minutes=4),
                sequence=2, source_event_id="event-2", evidence_hash="hash-2",
            )),
            dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=4),
        )
        self.assertEqual(result.venue, "gate")
        self.assertEqual(result.market_type, "spot")
        self.assertEqual(len(result.bars), 2)
        self.assertEqual(result.quality.status.value, "complete")

    def test_rejects_mixed_snapshot_or_scope(self):
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset((candle(snapshot_id="other"),), dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=4))
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset(
                (candle(), candle(
                    instrument_id="ETH_USDT", sequence=2, source_event_id="event-2", evidence_hash="hash-2",
                    open_time=UTC + timedelta(minutes=3), close_time=UTC + timedelta(minutes=4),
                    occurred_at=UTC + timedelta(minutes=3), observed_at=UTC + timedelta(minutes=4),
                )),
                dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=4),
            )

    def test_rejects_late_duplicate_and_out_of_order_facts(self):
        late = candle(observed_at=UTC + timedelta(hours=1))
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset((late,), dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=4))
        duplicate = candle(source_event_id="event-2", sequence=1, evidence_hash="hash-2")
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset((candle(), duplicate), dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=4))
        out_of_order = candle(sequence=0, source_event_id="event-0", evidence_hash="hash-0")
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset((candle(), out_of_order), dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=4))
        gap = candle(sequence=4, source_event_id="event-4", evidence_hash="hash-4", open_time=UTC + timedelta(minutes=5), close_time=UTC + timedelta(minutes=6), occurred_at=UTC + timedelta(minutes=5), observed_at=UTC + timedelta(minutes=6))
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset((candle(), candle(sequence=2, source_event_id="event-2", evidence_hash="hash-2", open_time=UTC + timedelta(minutes=3), close_time=UTC + timedelta(minutes=4), occurred_at=UTC + timedelta(minutes=3), observed_at=UTC + timedelta(minutes=4)), gap), dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=6))

    def test_requires_explicit_tuple_and_utc_cutoff(self):
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset([candle()], dataset_snapshot_id="dataset-1", as_of=UTC + timedelta(minutes=4))
        with self.assertRaises(M.GateBacktestDatasetError):
            M.build_gate_backtest_dataset((candle(),), dataset_snapshot_id="dataset-1", as_of=datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
