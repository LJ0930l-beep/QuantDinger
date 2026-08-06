import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load():
    names = [
        "app", "app.domain", "app.domain.decimal_values", "app.domain.gate_testnet_readiness_contracts", "app.domain.gate_readonly_contracts",
        "app.domain.gate_read_formatters", "app.domain.gate_read_transport_contracts",
        "app.domain.gate_readonly_adapter_contracts", "app.services",
        "app.services.gate_testnet_readiness_service",
    ]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        paths = {
            names[2]: ROOT / "app/domain/decimal_values.py",
            names[3]: ROOT / "app/domain/gate_testnet_readiness_contracts.py",
            names[4]: ROOT / "app/domain/gate_readonly_contracts.py",
            names[5]: ROOT / "app/domain/gate_read_formatters.py",
            names[6]: ROOT / "app/domain/gate_read_transport_contracts.py",
            names[7]: ROOT / "app/domain/gate_readonly_adapter_contracts.py",
            names[9]: ROOT / "app/services/gate_testnet_readiness_service.py",
        }
        for name in (names[2], names[3], names[4], names[5], names[6], names[7], names[9]):
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[9]], sys.modules[names[7]], sys.modules[names[4]], sys.modules[names[6]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, ADAPTER, RO, TRANSPORT = load()


def profile(**changes):
    values = dict(environment=RO.GateEnvironment.TESTNET, market_type=RO.GateMarketType.SPOT, credential_ref="opaque-ref")
    values.update(changes)
    return RO.GateReadCapabilityProfile(**values)


class GateTestnetReadinessServiceTests(unittest.TestCase):
    def test_valid_profile_is_ready_without_network_io(self):
        calls = []
        adapter = ADAPTER.GateReadonlyAdapter(profile(), lambda request: calls.append(request) or TRANSPORT.GateReadResponse(200, []))
        receipt = M.GateTestnetReadinessService().assess(adapter)
        self.assertEqual(receipt.status, M.GateTestnetReadinessStatus.READY)
        self.assertFalse(receipt.live_enabled)
        self.assertFalse(receipt.writes_enabled)
        self.assertEqual(calls, [])

    def test_non_public_profile_fails_at_adapter_boundary(self):
        disabled = profile(supports_public_market_data=False)
        with self.assertRaises(ADAPTER.GateReadonlyAdapterError):
            ADAPTER.GateReadonlyAdapter(disabled, lambda _request: TRANSPORT.GateReadResponse(200, []))

    def test_readiness_receipt_cannot_enable_live_or_write(self):
        with self.assertRaises(M.GateTestnetReadinessError):
            M.GateTestnetReadinessReceipt(
                M.GateTestnetReadinessStatus.READY, RO.GateMarketType.SPOT,
                "https://api-testnet.gateapi.io", True, True, False, ("bad",),
            )


if __name__ == "__main__":
    unittest.main()
