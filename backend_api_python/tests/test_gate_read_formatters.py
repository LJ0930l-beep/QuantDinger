import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    names = ["app", "app.domain", "app.domain.multi_asset_capability_contracts", "app.domain.gate_vertical_read_contracts", "app.domain.gate_read_formatters"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/multi_asset_capability_contracts.py"), (names[3], ROOT / "app/domain/gate_vertical_read_contracts.py"), (names[4], ROOT / "app/domain/gate_read_formatters.py")):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[4]], sys.modules[names[2]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, C = load()


class GateReadFormatterTests(unittest.TestCase):
    def test_balance_formatter_is_decimal_and_scoped(self):
        rows = M.normalize_gate_balances([{"currency": "USDT", "total": "10.000", "available": "9", "locked": "1"}], market_type=C.AssetMarketType.SPOT, account_scope="paper", valuation_ccy="usdt", observed_at=NOW, source_event_prefix="balance", evidence_hash_prefix="hash")
        self.assertEqual(rows[0].asset, "USDT"); self.assertEqual(rows[0].total, Decimal("10")); self.assertEqual(rows[0].available + rows[0].locked, rows[0].total)

    def test_position_formatter_is_perpetual_only_and_typed(self):
        rows = M.normalize_gate_positions([{"contract": "BTC_USDT", "side": "long", "size": "1", "entry_price": "100", "mark_price": "101", "leverage": "3", "margin_mode": "cross"}], market_type=C.AssetMarketType.PERPETUAL, account_scope="paper", observed_at=NOW, source_event_prefix="position")
        self.assertEqual(rows[0].instrument_id, "BTC_USDT"); self.assertEqual(rows[0].quantity, Decimal("1"))
        with self.assertRaises(M.GateReadPayloadError): M.normalize_gate_positions([], market_type=C.AssetMarketType.SPOT, account_scope="paper", observed_at=NOW, source_event_prefix="position")

    def test_instrument_formatter_and_missing_fields_fail_closed(self):
        rows = M.normalize_gate_instruments({"data": [{"name": "BTC_USDT", "order_price_round": "0.1", "order_size_increment": "0.001", "order_size_min": "0.001", "min_notional": "5"}]}, market_type=C.AssetMarketType.SPOT, observed_at=NOW, rule_version="gate-v1")
        self.assertEqual(rows[0].tick_size, Decimal("0.1"))
        with self.assertRaises(M.GateReadPayloadError): M.normalize_gate_instruments([{"name": "BTC_USDT"}], market_type=C.AssetMarketType.SPOT, observed_at=NOW, rule_version="gate-v1")

    def test_error_mapping_never_returns_not_found(self):
        self.assertEqual(M.classify_gate_response_error(401), M.GateReadErrorKind.AUTH_OR_PERMISSION)
        self.assertEqual(M.classify_gate_response_error(429), M.GateReadErrorKind.RATE_LIMIT)
        self.assertEqual(M.classify_gate_response_error(503), M.GateReadErrorKind.TEMPORARY)
        self.assertEqual(M.classify_gate_response_error(400), M.GateReadErrorKind.INVALID_RESPONSE)
        with self.assertRaises(M.GateReadPayloadError): M.classify_gate_response_error("401")


if __name__ == "__main__": unittest.main()
