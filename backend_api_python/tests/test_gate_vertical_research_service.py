import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)


def load():
    names = [
        "app", "app.domain", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_vertical_read_contracts", "app.domain.gate_read_formatters",
        "app.services", "app.services.gate_vertical_research_service",
    ]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        paths = {
            names[2]: ROOT / "app/domain/multi_asset_capability_contracts.py",
            names[3]: ROOT / "app/domain/gate_vertical_read_contracts.py",
            names[4]: ROOT / "app/domain/gate_read_formatters.py",
            names[6]: ROOT / "app/services/gate_vertical_research_service.py",
        }
        for name in (names[2], names[3], names[4], names[6]):
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[6]], sys.modules[names[3]], sys.modules[names[2]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, V, CAP = load()


def auth(market_type):
    return V.GateAuthFacts(
        "gate", market_type, CAP.CapabilityEnvironment.TESTNET, "acct-1", "opaque-ref",
        (V.GatePermission.READ_ACCOUNT,), "auth-v1", UTC,
    )


BALANCES = [{"asset": "USDT", "total": "1000", "available": "900", "locked": "100"}]
INSTRUMENTS = [{"instrument_id": "BTC_USDT", "tick_size": "0.1", "quantity_step": "0.001", "minimum_quantity": "0.001", "minimum_notional": "5"}]


class GateVerticalResearchServiceTests(unittest.TestCase):
    def test_spot_bundle_is_decimal_safe_and_deterministic(self):
        service = M.GateVerticalResearchService("fixture", "evidence")
        first = service.assemble(auth(CAP.AssetMarketType.SPOT), balances_payload=BALANCES, instruments_payload=INSTRUMENTS, rule_version="rules-1", observed_at=UTC)
        second = service.assemble(auth(CAP.AssetMarketType.SPOT), balances_payload=BALANCES, instruments_payload=INSTRUMENTS, rule_version="rules-1", observed_at=UTC)
        self.assertEqual(first.bundle_fingerprint, second.bundle_fingerprint)
        self.assertEqual(first.balances[0].total, 1000)
        self.assertEqual(first.positions, ())

    def test_perpetual_bundle_requires_and_scopes_positions(self):
        positions = [{"contract": "BTC_USDT", "side": "long", "size": "1", "entry_price": "100", "mark": "101", "leverage": "2", "margin_mode": "cross"}]
        bundle = M.GateVerticalResearchService("fixture", "evidence").assemble(
            auth(CAP.AssetMarketType.PERPETUAL), balances_payload=BALANCES, instruments_payload=INSTRUMENTS,
            positions_payload=positions, rule_version="rules-1", observed_at=UTC,
        )
        self.assertEqual(bundle.positions[0].instrument_id, "BTC_USDT")

    def test_invalid_position_scope_or_missing_perpetual_payload_fails_closed(self):
        service = M.GateVerticalResearchService("fixture", "evidence")
        with self.assertRaises(M.GateVerticalResearchServiceError):
            service.assemble(auth(CAP.AssetMarketType.PERPETUAL), balances_payload=BALANCES, instruments_payload=INSTRUMENTS, rule_version="rules-1", observed_at=UTC)
        with self.assertRaises(M.GateVerticalResearchServiceError):
            service.assemble(auth(CAP.AssetMarketType.SPOT), balances_payload=BALANCES, instruments_payload=INSTRUMENTS, positions_payload=[{"contract": "BTC_USDT"}], rule_version="rules-1", observed_at=UTC)


if __name__ == "__main__":
    unittest.main()
