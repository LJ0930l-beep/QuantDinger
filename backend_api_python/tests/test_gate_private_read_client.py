from __future__ import annotations

import hashlib
import hmac
import importlib.util
from pathlib import Path
from types import ModuleType
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = ("app", "app.domain", "app.domain.multi_asset_capability_contracts", "app.services", "app.services.gate_private_read_client")
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        for name, path in ((names[2], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py"),
                           (names[4], ROOT / "app" / "services" / "gate_private_read_client.py")):
            spec = importlib.util.spec_from_file_location(name, path); assert spec and spec.loader
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[2]], sys.modules[names[4]]
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = original


C, M = _load()


class Transport:
    def __init__(self, status=200, payload=None): self.status, self.payload, self.calls = status, payload or [], []
    def request(self, method, path, query, body, headers):
        self.calls.append((method, path, query, body, headers)); return self.status, self.payload


class GatePrivateReadClientTests(unittest.TestCase):
    def _client(self, transport=None):
        credential = M.GatePrivateCredential("key-example", "secret-example", C.CapabilityEnvironment.TESTNET)
        return M.GatePrivateReadClient(credential, transport or Transport(), timestamp_provider=lambda: 1700000000)

    def test_signature_is_gate_v4_shape_and_secret_is_not_repr(self):
        transport = Transport(payload=[{"currency": "USDT", "available": "1", "locked": "0", "balance": "1"}])
        client = self._client(transport)
        self.assertEqual(client.read_spot_accounts(), transport.payload)
        method, path, query, body, headers = transport.calls[0]
        expected = "\n".join((method, path, query, hashlib.sha512(body.encode()).hexdigest(), "1700000000"))
        expected_sig = hmac.new(b"secret-example", expected.encode(), hashlib.sha512).hexdigest()
        self.assertEqual(headers["SIGN"], expected_sig)
        self.assertNotIn("secret-example", repr(client.credential))

    def test_instrument_reads_use_get_only_gate_paths(self):
        transport = Transport(payload=[])
        client = self._client(transport)
        self.assertEqual(client.read_spot_instruments(), [])
        self.assertEqual(transport.calls[0][1], "/api/v4/spot/currency_pairs")
        self.assertEqual(client.read_futures_instruments(), [])
        self.assertEqual(transport.calls[1][1], "/api/v4/futures/usdt/contracts")
        self.assertTrue(all(call[0] == "GET" and call[3] == "" for call in transport.calls))

    def test_auth_permission_and_temporary_errors_are_typed(self):
        with self.assertRaises(M.GatePrivateReadAuthError):
            self._client(Transport(status=401)).read_spot_accounts()
        with self.assertRaises(M.GatePrivateReadPermissionError):
            self._client(Transport(status=403)).read_spot_accounts()
        with self.assertRaises(M.GatePrivateReadTemporaryError):
            self._client(Transport(status=503)).read_spot_accounts()

    def test_disabled_default_transport_never_networks(self):
        credential = M.GatePrivateCredential("key-example", "secret-example", C.CapabilityEnvironment.PAPER)
        with self.assertRaises(M.GatePrivateReadError):
            M.GatePrivateReadClient.disabled(credential, timestamp_provider=lambda: 1).read_spot_accounts()

    def test_order_history_uses_finished_status_and_is_typed(self):
        transport = Transport(payload=[])
        client = self._client(transport)
        self.assertEqual(client.read_spot_order_history(currency_pair="BTC_USDT"), [])
        self.assertEqual(transport.calls[0][1], "/api/v4/spot/orders")
        self.assertEqual(transport.calls[0][2], "currency_pair=BTC_USDT&status=finished")
        with self.assertRaises(M.GatePrivateReadInvalidResponse):
            client.read_spot_order_history(currency_pair="BTC_USDT", status="all")

    def test_futures_account_book_is_read_only_and_scoped(self):
        transport = Transport(payload=[])
        client = self._client(transport)
        self.assertEqual(client.read_futures_account_book(contract="BTC_USDT", limit=50, offset=10), [])
        self.assertEqual(transport.calls[0][1], "/api/v4/futures/usdt/account_book")
        self.assertEqual(transport.calls[0][2], "contract=BTC_USDT&limit=50&offset=10")
        with self.assertRaises(M.GatePrivateReadError):
            client.read_futures_account_book(limit=0)


if __name__ == "__main__": unittest.main()
