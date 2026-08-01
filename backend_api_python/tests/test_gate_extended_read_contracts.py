import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]; UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    name = "app.domain.gate_extended_read_contracts"; names = ["app", "app.domain", "app.domain.multi_asset_capability_contracts", name]; old = {n: sys.modules.get(n) for n in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]; sys.modules["app"] = app; sys.modules["app.domain"] = domain
        ms = importlib.util.spec_from_file_location(names[2], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py"); multi = importlib.util.module_from_spec(ms); sys.modules[names[2]] = multi; ms.loader.exec_module(multi)
        spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domain" / "gate_extended_read_contracts.py"); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module, multi
    finally:
        for n in reversed(names):
            if old[n] is None: sys.modules.pop(n, None)
            else: sys.modules[n] = old[n]


M, MULTI = load()


class GateExtendedTests(unittest.TestCase):
    def test_delivery_and_session_facts(self):
        delivery = M.GateDeliveryFact("BTC_USDT_20261231", Decimal("1"), UTC, UTC + timedelta(hours=1), None, UTC, "e1", "r1"); self.assertEqual(delivery.contract_size, Decimal("1"))
        session = M.GateEquitySessionFact("AAPL", UTC, UTC + timedelta(hours=6), "America/New_York", "corp-1", UTC, "e2"); self.assertEqual(session.instrument_id, "AAPL")

    def test_option_mark_is_options_only(self):
        option = M.GateOptionMarkFact("BTC-20261231-100000-C", "BTC_USDT", MULTI.AssetMarketType.OPTIONS, M.OptionRight.CALL, Decimal("100000"), UTC + timedelta(days=30), Decimal("100"), Decimal("0.5"), UTC, "e3", "r1"); self.assertEqual(option.right, M.OptionRight.CALL)
        with self.assertRaises(M.GateExtendedReadError): M.GateOptionMarkFact(**{**option.__dict__, "market_type": MULTI.AssetMarketType.SPOT})

    def test_time_and_decimal_bounds_fail_closed(self):
        with self.assertRaises(M.GateExtendedReadError): M.GateDeliveryFact("x", 1.0, UTC, UTC + timedelta(hours=1), None, UTC, "e", "r")
        with self.assertRaises(M.GateExtendedReadError): M.GateEquitySessionFact("x", UTC + timedelta(hours=1), UTC, "UTC", "c", UTC, "e")

    def test_fingerprint_is_stable(self):
        left = M.GateOptionMarkFact("o", "u", MULTI.AssetMarketType.OPTIONS, M.OptionRight.PUT, Decimal("100"), UTC + timedelta(days=1), Decimal("2.00"), Decimal("0.1"), UTC, "e", "r")
        right = M.GateOptionMarkFact("o", "u", MULTI.AssetMarketType.OPTIONS, M.OptionRight.PUT, Decimal("100"), UTC + timedelta(days=1), Decimal("2"), Decimal("0.10"), UTC, "e", "r")
        self.assertEqual(M.gate_extended_fingerprint(left), M.gate_extended_fingerprint(right))


if __name__ == "__main__": unittest.main()
