import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def load():
    names = [
        "app", "app.domain", "app.domain.gate_readonly_contracts",
        "app.domain.gate_read_formatters", "app.domain.gate_read_transport_contracts",
    ]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        paths = {
            names[2]: ROOT / "app/domain/gate_readonly_contracts.py",
            names[3]: ROOT / "app/domain/gate_read_formatters.py",
            names[4]: ROOT / "app/domain/gate_read_transport_contracts.py",
        }
        # Formatter imports vertical contracts; load its dependency first.
        extra = "app.domain.gate_vertical_read_contracts"
        old_extra = sys.modules.get(extra)
        spec = importlib.util.spec_from_file_location(extra, ROOT / "app/domain/gate_vertical_read_contracts.py")
        mod = importlib.util.module_from_spec(spec); sys.modules[extra] = mod; spec.loader.exec_module(mod)
        for name in names[2:]:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[4]], sys.modules[names[2]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]
        sys.modules.pop("app.domain.gate_vertical_read_contracts", None)


M, RO = load()


def profile(market_type):
    return RO.GateReadCapabilityProfile(RO.GateEnvironment.TESTNET, market_type, credential_ref="opaque-ref")


class GateReadTransportTests(unittest.TestCase):
    def test_public_request_is_get_only_and_profile_scoped(self):
        req = M.GateReadRequest(M.GateMarketType.SPOT, M.GatePublicReadEndpoint.CANDLES if hasattr(M.GatePublicReadEndpoint, "CANDLES") else M.GatePublicReadEndpoint.CANDLESTICKS, "BTC_USDT", (("interval", "1m"),))
        self.assertEqual(req.path, "/spot/candlesticks")
        self.assertEqual(req.params["currency_pair"], "BTC_USDT")
        self.assertEqual(M.validate_gate_read_request(req, profile(RO.GateMarketType.SPOT)), req)
        with self.assertRaises(M.GateReadTransportError):
            M.validate_gate_read_request(req, profile(RO.GateMarketType.PERPETUAL))

    def test_query_is_canonical_and_instrument_required(self):
        with self.assertRaises(M.GateReadTransportError):
            M.GateReadRequest(M.GateMarketType.SPOT, M.GatePublicReadEndpoint.TICKERS)
        with self.assertRaises(M.GateReadTransportError):
            M.GateReadRequest(M.GateMarketType.SPOT, M.GatePublicReadEndpoint.TICKERS, "BTC_USDT", (("z", "1"), ("a", "2")))

    def test_error_response_is_typed_and_never_not_found(self):
        response = M.GateReadResponse(429, {"label": "TOO_FAST"}, M.GateReadErrorKind.RATE_LIMIT)
        self.assertEqual(response.error_kind, M.GateReadErrorKind.RATE_LIMIT)
        with self.assertRaises(M.GateReadTransportError):
            M.GateReadResponse(500, {}, M.GateReadErrorKind.INVALID_RESPONSE)

    def test_response_is_immutable_and_success_has_no_error_kind(self):
        response = M.GateReadResponse(200, [], None)
        self.assertIsNone(response.error_kind)
        with self.assertRaises(M.GateReadTransportError):
            M.GateReadResponse(401, {}, M.GateReadErrorKind.INVALID_RESPONSE)


if __name__ == "__main__":
    unittest.main()
