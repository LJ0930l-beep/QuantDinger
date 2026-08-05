import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]; UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    name = "app.domain.gate_margin_read_contracts"; names = ["app", "app.domain", "app.domain.multi_asset_capability_contracts", name]; old = {n: sys.modules.get(n) for n in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]; sys.modules["app"] = app; sys.modules["app.domain"] = domain
        ms = importlib.util.spec_from_file_location(names[2], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py"); multi = importlib.util.module_from_spec(ms); sys.modules[names[2]] = multi; ms.loader.exec_module(multi)
        spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domain" / "gate_margin_read_contracts.py"); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module, multi
    finally:
        for n in reversed(names):
            if old[n] is None: sys.modules.pop(n, None)
            else: sys.modules[n] = old[n]


M, MULTI = load()


def tier(**changes):
    facts = dict(instrument_id="BTC_USDT", tier=1, notional_floor=Decimal("0"), notional_ceiling=Decimal("100000"), max_leverage=Decimal("10"), maintenance_margin_rate=Decimal("0.05"), rule_version="r1"); facts.update(changes); return M.GateLeverageTier(**facts)


def snapshot(**changes):
    facts = dict(market_type=MULTI.AssetMarketType.PERPETUAL, account_scope="paper", instrument_id="BTC_USDT", margin_currency="USDT", equity=Decimal("1000"), available_margin=Decimal("800"), used_margin=Decimal("200"), maintenance_margin=Decimal("50"), leverage_tiers=(tier(),), observed_at=UTC, source_event_id="e1", evidence_hash="h1"); facts.update(changes); return M.GateMarginSnapshot(**facts)


class GateMarginTests(unittest.TestCase):
    def test_perpetual_snapshot_and_fingerprint(self):
        self.assertEqual(M.gate_margin_fingerprint(snapshot(equity=Decimal("1000.00"))), M.gate_margin_fingerprint(snapshot(equity=Decimal("1000"))))

    def test_spot_and_incomplete_tiers_fail_closed(self):
        with self.assertRaises(M.GateMarginContractError): snapshot(market_type=MULTI.AssetMarketType.SPOT)
        with self.assertRaises(M.GateMarginContractError): snapshot(leverage_tiers=())

    def test_margin_and_tier_bounds(self):
        with self.assertRaises(M.GateMarginContractError): snapshot(available_margin=Decimal("900"))
        with self.assertRaises(M.GateMarginContractError): tier(notional_ceiling=Decimal("0"))

    def test_decimal_and_utc_are_strict(self):
        with self.assertRaises(M.GateMarginContractError): snapshot(equity=1.0)
        with self.assertRaises(M.GateMarginContractError): snapshot(observed_at=datetime(2026, 1, 1))


if __name__ == "__main__": unittest.main()
