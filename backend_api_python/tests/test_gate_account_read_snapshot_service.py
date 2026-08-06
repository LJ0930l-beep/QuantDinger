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
            instruments=[{"name": "BTC_USDT", "order_price_round": "0.1", "order_size_increment": "0.001", "order_size_min": "0.001", "min_notional": "5"}],
            valuation_ccy="USDT", observed_at=NOW,
        )
        self.assertEqual(len(snapshot.balances), 1)
        self.assertEqual(len(snapshot.orders), 1)
        self.assertEqual(len(snapshot.fills), 1)
        self.assertEqual(snapshot.to_public_dict()["orders"][0]["filled_quantity"], "1")
        self.assertEqual(snapshot.to_public_dict()["fills"][0]["venue_fill_id"], "f-1")
        self.assertEqual(snapshot.to_public_dict()["instruments"][0]["tick_size"], "0.1")
        self.assertEqual(snapshot.to_public_dict()["instruments"][0]["quantity_step"], "0.001")
        self.assertFalse("opaque-ref" in repr(snapshot.to_public_dict()))

    def test_missing_stable_fill_id_fails_closed(self):
        with self.assertRaises(GateAccountReadSnapshotError):
            GateAccountReadSnapshotService().read_from_payloads(
                self.auth,
                balances=[{"currency": "USDT", "total": "1", "available": "1", "locked": "0"}],
                fills=[{"contract": "BTC_USDT", "order_id": "o-1", "side": "buy", "size": "1", "price": "100"}],
                valuation_ccy="USDT", observed_at=NOW,
            )

    def test_perpetual_position_pnl_evidence_survives_snapshot_boundary(self):
        snapshot = GateAccountReadSnapshotService().read_from_payloads(
            self.auth,
            balances=[{"currency": "USDT", "total": "100", "available": "90", "locked": "10"}],
            positions=[{
                "contract": "BTC_USDT", "side": "long", "size": "1",
                "entry_price": "100", "mark_price": "101", "leverage": "3",
                "margin_mode": "cross", "unrealised_pnl": "1.5",
                "realised_pnl": "-0.25", "funding_fee": "0.01",
            }],
            valuation_ccy="USDT", observed_at=NOW,
        )
        public = snapshot.to_public_dict()
        self.assertEqual(public["pnl"]["unrealized"], "1.5")
        self.assertEqual(public["pnl"]["realized"], "-0.25")
        self.assertEqual(public["pnl"]["funding"], "0.01")

    def test_account_book_categories_survive_snapshot_boundary(self):
        snapshot = GateAccountReadSnapshotService().read_from_payloads(
            self.auth,
            balances=[{"currency": "USDT", "total": "100", "available": "90", "locked": "10"}],
            account_book=[
                {"id": "book-pnl", "type": "pnl", "change": "2.5", "balance": "102.5", "time": "1785671999", "contract": "BTC_USDT", "trade_id": "trade-1"},
                {"id": "book-fee", "type": "fee", "change": "-0.1", "balance": "102.4", "time": "1785672000"},
                {"id": "book-fund", "type": "fund", "change": "-0.2", "balance": "102.2", "time": "1785672001"},
            ],
            valuation_ccy="USDT", observed_at=datetime(2026, 8, 2, 12, 0, 2, tzinfo=timezone.utc),
        )
        public = snapshot.to_public_dict()
        self.assertEqual(public["account_book_count"], 3)
        self.assertEqual(public["account_book_totals"]["pnl"], "2.5")
        self.assertEqual(public["account_book_totals"]["fee"], "-0.1")
        self.assertEqual(public["account_book_totals"]["fund"], "-0.2")


if __name__ == "__main__":
    unittest.main()
