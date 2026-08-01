import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load():
    names = ["app", "app.domain", "app.domain.gate_readonly_contracts", "app.domain.gate_read_formatters", "app.domain.gate_read_transport_contracts", "app.domain.gate_readonly_adapter_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        vertical = "app.domain.gate_vertical_read_contracts"
        spec = importlib.util.spec_from_file_location(vertical, ROOT / "app/domain/gate_vertical_read_contracts.py")
        module = importlib.util.module_from_spec(spec); sys.modules[vertical] = module; spec.loader.exec_module(module)
        paths = {names[2]: ROOT / "app/domain/gate_readonly_contracts.py", names[3]: ROOT / "app/domain/gate_read_formatters.py", names[4]: ROOT / "app/domain/gate_read_transport_contracts.py", names[5]: ROOT / "app/domain/gate_readonly_adapter_contracts.py"}
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[5]], sys.modules[names[2]], sys.modules[names[4]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]
        sys.modules.pop("app.domain.gate_vertical_read_contracts", None)


M, RO, TRANSPORT = load()


def profile():
    return RO.GateReadCapabilityProfile(RO.GateEnvironment.TESTNET, RO.GateMarketType.SPOT, credential_ref="opaque-ref")


class GateReadonlyAdapterTests(unittest.TestCase):
    def test_injected_transport_receives_only_typed_public_get_request(self):
        seen = []
        def transport(request):
            seen.append(request)
            return TRANSPORT.GateReadResponse(200, [{"currency_pair": "BTC_USDT"}])
        adapter = M.GateReadonlyAdapter(profile(), transport)
        response = adapter.candles("BTC_USDT", interval="1m", limit=10)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen[0].path, "/spot/candlesticks")
        self.assertEqual(seen[0].params["limit"], "10")

    def test_transport_failures_are_typed_and_payloads_are_not_leaked(self):
        def transport(_request):
            raise RuntimeError("secret-looking transport detail")
        with self.assertRaises(M.GateReadonlyAdapterError) as caught:
            M.GateReadonlyAdapter(profile(), transport).order_book("BTC_USDT")
        self.assertNotIn("secret-looking", str(caught.exception))

    def test_non_response_and_invalid_limits_fail_closed(self):
        with self.assertRaises(M.GateReadonlyAdapterError):
            M.GateReadonlyAdapter(profile(), lambda _request: {}) .candles("BTC_USDT")
        adapter = M.GateReadonlyAdapter(profile(), lambda _request: TRANSPORT.GateReadResponse(200, []))
        with self.assertRaises(M.GateReadonlyAdapterError):
            adapter.candles("BTC_USDT", limit=0)
        with self.assertRaises(M.GateReadonlyAdapterError):
            adapter.order_book("BTC_USDT", limit=1001)

    def test_profile_without_public_market_capability_is_rejected(self):
        disabled = RO.GateReadCapabilityProfile(RO.GateEnvironment.TESTNET, RO.GateMarketType.SPOT, credential_ref="opaque-ref", supports_public_market_data=False)
        with self.assertRaises(M.GateReadonlyAdapterError):
            M.GateReadonlyAdapter(disabled, lambda _request: TRANSPORT.GateReadResponse(200, []))


if __name__ == "__main__":
    unittest.main()
