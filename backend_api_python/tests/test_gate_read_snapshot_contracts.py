"""Pure tests for Gate read-only snapshot assembly."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contracts() -> SimpleNamespace:
    names = (
        "app", "app.domain", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts", "app.domain.gate_vertical_read_contracts",
        "app.domain.gate_read_snapshot_contracts",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        multi = _load(names[2], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        market = _load(names[3], ROOT / "app" / "domain" / "gate_market_read_contracts.py")
        vertical = _load(names[4], ROOT / "app" / "domain" / "gate_vertical_read_contracts.py")
        snapshot = _load(names[5], ROOT / "app" / "domain" / "gate_read_snapshot_contracts.py")
        return SimpleNamespace(multi=multi, market=market, vertical=vertical, snapshot=snapshot)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


C = _contracts()


NOW = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _auth(market_type=C.multi.AssetMarketType.SPOT):
    return C.vertical.GateAuthFacts("gate", market_type, C.multi.CapabilityEnvironment.PAPER, "paper-main", "credential-ref", (C.vertical.GatePermission.READ_MARKET, C.vertical.GatePermission.READ_ACCOUNT), "auth-v1", NOW)


def _balance(market_type=C.multi.AssetMarketType.SPOT):
    return C.vertical.GateBalanceFact("gate", market_type, "paper-main", "USDT", Decimal("100"), Decimal("80"), Decimal("20"), "USDT", NOW, "balance-1", "e" * 64)


def _candle(market_type=C.multi.AssetMarketType.SPOT, observed_at=NOW):
    return C.market.GateCandleFact(market_type, "BTC_USDT", "1m", NOW - timedelta(minutes=1), NOW, Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100.5"), Decimal("10"), NOW, observed_at, 1, "candle-1", "snapshot-1", "rules-v1", "f" * 64)


def _order():
    return C.vertical.GateOrderFact("gate", C.multi.AssetMarketType.PERPETUAL, "paper-main", "BTC_USDT", "order-1", "client-1", C.vertical.GateOrderSide.BUY, C.vertical.GateOrderStatus.OPEN, Decimal("2"), Decimal("1"), Decimal("100"), NOW, "order-event-1")


def _fill():
    return C.vertical.GateFillFact("gate", C.multi.AssetMarketType.PERPETUAL, "paper-main", "BTC_USDT", "order-1", "fill-1", C.vertical.GateOrderSide.BUY, Decimal("1"), Decimal("100"), "USDT", Decimal("0.1"), NOW, "fill-event-1")


class GateReadSnapshotContractTests(unittest.TestCase):
    def test_assembles_scoped_immutable_snapshot_and_safe_public_summary(self):
        snapshot = C.snapshot.build_gate_read_snapshot(_auth(), (_balance(),), market_facts=(_candle(),), observed_at=NOW)
        self.assertEqual(snapshot.to_public_dict()["market_type"], "spot")
        self.assertNotIn("credential-ref", repr(snapshot.to_public_dict()))
        with self.assertRaises((AttributeError, TypeError)):
            snapshot.observed_at = NOW

    def test_scope_mismatch_is_fail_closed(self):
        with self.assertRaises(C.snapshot.GateReadSnapshotError):
            C.snapshot.build_gate_read_snapshot(_auth(), (_balance(C.multi.AssetMarketType.PERPETUAL),), observed_at=NOW)

    def test_future_fact_observation_is_rejected(self):
        with self.assertRaises(C.snapshot.GateReadSnapshotError):
            C.snapshot.build_gate_read_snapshot(_auth(), market_facts=(_candle(observed_at=NOW.replace(minute=1)),), observed_at=NOW)

    def test_fingerprint_is_deterministic_and_changes_with_fact(self):
        first = C.snapshot.build_gate_read_snapshot(_auth(), (_balance(),), market_facts=(_candle(),), observed_at=NOW)
        second = C.snapshot.build_gate_read_snapshot(_auth(), (_balance(),), market_facts=(_candle(),), observed_at=NOW)
        changed = C.snapshot.build_gate_read_snapshot(_auth(), (_balance(),), market_facts=(_candle(observed_at=NOW),), observed_at=NOW.replace(second=1))
        self.assertEqual(first.snapshot_fingerprint, second.snapshot_fingerprint)
        self.assertNotEqual(first.snapshot_fingerprint, changed.snapshot_fingerprint)

    def test_orders_and_fills_are_scoped_and_fingerprinted(self):
        auth = _auth(C.multi.AssetMarketType.PERPETUAL)
        first = C.snapshot.build_gate_read_snapshot(auth, positions=(), orders=(_order(),), fills=(_fill(),), observed_at=NOW)
        second = C.snapshot.build_gate_read_snapshot(auth, positions=(), orders=(_order(),), fills=(_fill(),), observed_at=NOW)
        self.assertEqual(first.snapshot_fingerprint, second.snapshot_fingerprint)
        self.assertEqual(first.to_public_dict()["order_count"], 1)
        self.assertEqual(first.to_public_dict()["fill_count"], 1)


if __name__ == "__main__":
    unittest.main()
