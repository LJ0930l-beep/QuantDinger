from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from types import ModuleType
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = [
        "app", "app.domain", "app.services", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_market_read_contracts", "app.domain.gate_vertical_read_contracts",
        "app.domain.gate_read_snapshot_contracts", "app.domain.gate_read_formatters",
        "app.services.gate_account_read_snapshot_service",
    ]
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        for name, rel in (
            (names[3], "app/domain/multi_asset_capability_contracts.py"),
            (names[4], "app/domain/gate_market_read_contracts.py"),
            (names[5], "app/domain/gate_vertical_read_contracts.py"),
            (names[6], "app/domain/gate_read_snapshot_contracts.py"),
            (names[7], "app/domain/gate_read_formatters.py"),
            (names[8], "app/services/gate_account_read_snapshot_service.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, ROOT / rel)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[5]], sys.modules[names[3]], sys.modules[names[8]]
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = original


V, M, S = _load()
GateAuthFacts, GatePermission = V.GateAuthFacts, V.GatePermission
AssetMarketType, CapabilityEnvironment = M.AssetMarketType, M.CapabilityEnvironment
GateAccountReadSnapshotError, GateAccountReadSnapshotService = S.GateAccountReadSnapshotError, S.GateAccountReadSnapshotService


NOW = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)


class GateAccountReadSnapshotServiceTests(unittest.TestCase):
    def setUp(self):
        self.auth = GateAuthFacts(
            "gate", AssetMarketType.PERPETUAL, CapabilityEnvironment.PAPER,
            "paper-main", "opaque-ref", (GatePermission.READ_ACCOUNT,), "auth-v1", NOW,
        )

    def test_composes_balances_orders_and_fills_without_transport(self):
        snapshot = GateAccountReadSnapshotService().read_from_payloads(
            self.auth,
            balances=[{"currency": "USDT", "total": "100", "available": "90", "locked": "10"}],
            orders=[{"contract": "BTC_USDT", "id": "o-1", "side": "buy", "status": "open", "size": "2", "filled": "1"}],
            fills=[{"contract": "BTC_USDT", "order_id": "o-1", "trade_id": "f-1", "side": "buy", "size": "1", "price": "100"}],
            valuation_ccy="USDT", observed_at=NOW,
        )
        self.assertEqual(len(snapshot.balances), 1)
        self.assertEqual(len(snapshot.orders), 1)
        self.assertEqual(len(snapshot.fills), 1)
        self.assertEqual(snapshot.to_public_dict()["orders"][0]["filled_quantity"], "1")
        self.assertEqual(snapshot.to_public_dict()["fills"][0]["venue_fill_id"], "f-1")
        self.assertFalse("opaque-ref" in repr(snapshot.to_public_dict()))

    def test_missing_stable_fill_id_fails_closed(self):
        with self.assertRaises(GateAccountReadSnapshotError):
            GateAccountReadSnapshotService().read_from_payloads(
                self.auth,
                balances=[{"currency": "USDT", "total": "1", "available": "1", "locked": "0"}],
                fills=[{"contract": "BTC_USDT", "order_id": "o-1", "side": "buy", "size": "1", "price": "100"}],
                valuation_ccy="USDT", observed_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
