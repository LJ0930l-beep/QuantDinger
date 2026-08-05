from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _modules():
    names = (
        "app", "app.domain", "app.services", "app.domain.decimal_values",
        "app.domain.multi_asset_capability_contracts", "app.domain.gate_market_read_contracts",
        "app.domain.gate_vertical_read_contracts", "app.domain.gate_readonly_contracts",
        "app.domain.gate_read_snapshot_contracts", "app.domain.gate_read_formatters",
        "app.services.gate_private_read_client", "app.services.gate_private_read_http_transport",
        "app.services.gate_account_read_snapshot_service", "app.services.gate_private_read_account_service",
        "app.services.gate_testnet_credential_provider", "app.services.gate_testnet_env_read_service",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        _load(names[3], ROOT / "app" / "domain" / "decimal_values.py")
        _load(names[4], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        _load(names[5], ROOT / "app" / "domain" / "gate_market_read_contracts.py")
        _load(names[6], ROOT / "app" / "domain" / "gate_vertical_read_contracts.py")
        _load(names[7], ROOT / "app" / "domain" / "gate_readonly_contracts.py")
        _load(names[8], ROOT / "app" / "domain" / "gate_read_snapshot_contracts.py")
        _load(names[9], ROOT / "app" / "domain" / "gate_read_formatters.py")
        _load(names[10], ROOT / "app" / "services" / "gate_private_read_client.py")
        _load(names[11], ROOT / "app" / "services" / "gate_private_read_http_transport.py")
        _load(names[12], ROOT / "app" / "services" / "gate_account_read_snapshot_service.py")
        _load(names[13], ROOT / "app" / "services" / "gate_private_read_account_service.py")
        _load(names[14], ROOT / "app" / "services" / "gate_testnet_credential_provider.py")
        service = _load(names[15], ROOT / "app" / "services" / "gate_testnet_env_read_service.py")
        return service
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


SERVICE = _modules()


class _Response:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self._payload).encode("utf-8")


class _Opener:
    def __call__(self, request, timeout):
        path = request.full_url
        if path.endswith("/spot/accounts"):
            return _Response([{"currency": "USDT", "total": "10", "available": "10", "locked": "0"}])
        if path.endswith("/spot/currency_pairs"):
            return _Response([{
                "id": "BTC_USDT",
                "min_base_amount": "0.0001",
                "min_quote_amount": "1",
                "amount_precision": 4,
                "precision": 2,
            }])
        if path.endswith("/spot/open_orders?currency_pair=BTC_USDT"):
            return _Response([])
        if path.endswith("/spot/orders?currency_pair=BTC_USDT&status=finished"):
            return _Response([{"id": "test-order", "currency_pair": "BTC_USDT", "side": "buy", "amount": "0.0001", "price": "30000", "status": "cancelled", "text": "t-test"}])
        if path.endswith("/spot/my_trades?currency_pair=BTC_USDT"):
            return _Response([])
        if path.endswith("/futures/usdt/accounts"):
            return _Response({"total": "100", "available": "95"})
        if path.endswith("/futures/usdt/contracts"):
            return _Response([{
                "name": "BTC_USDT",
                "order_price_round": "0.1",
                "order_size_min": "0.0001",
                "min_quote_amount": "1",
                "enable_decimal": False,
            }])
        if path.endswith("/futures/usdt/positions"):
            return _Response([])
        if path.endswith("/futures/usdt/orders?contract=BTC_USDT&status=open"):
            return _Response([])
        if path.endswith("/futures/usdt/my_trades?contract=BTC_USDT"):
            return _Response([])
        if "/futures/usdt/account_book?" in path:
            return _Response([])
        raise AssertionError(path)


class GateTestnetEnvReadServiceTests(unittest.TestCase):
    def _env(self, **changes):
        values = {
            "QUANT_GATE_TESTNET_ENV_READ_ENABLED": "1",
            "GATE_TESTNET_API_KEY": "test-key",
            "GATE_TESTNET_API_SECRET": "test-secret",
            "AGENT_LIVE_TRADING_ENABLED": "false",
        }
        values.update(changes)
        return values

    def test_real_adapter_composes_typed_snapshot_without_secret(self):
        snapshot = SERVICE.read_gate_testnet_environment_snapshot(
            market_type="spot",
            account_scope="test-account",
            instrument_id="BTC_USDT",
            environ=self._env(),
            timestamp_provider=lambda: 1700000000,
            opener=_Opener(),
        )
        body = snapshot.to_public_dict()
        self.assertEqual(body["venue_id"], "gate")
        self.assertEqual(body["balance_count"], 1)
        self.assertEqual(body["instrument_count"], 1)
        self.assertNotIn("test-secret", json.dumps(body))

    def test_env_read_is_disabled_by_default(self):
        with self.assertRaises(SERVICE.GateTestnetEnvReadError):
            SERVICE.read_gate_testnet_environment_snapshot(
                market_type="spot", account_scope="test-account", environ=self._env(QUANT_GATE_TESTNET_ENV_READ_ENABLED="0")
            )

    def test_real_adapter_can_read_finished_orders(self):
        snapshot = SERVICE.read_gate_testnet_environment_snapshot(
            market_type="spot", account_scope="test-account", instrument_id="BTC_USDT",
            order_history=True, environ=self._env(), timestamp_provider=lambda: 1700000000, opener=_Opener(),
        )
        body = snapshot.to_public_dict()
        self.assertEqual(body["order_count"], 1)

    def test_real_adapter_reads_perpetual_rules_without_writes(self):
        snapshot = SERVICE.read_gate_testnet_environment_snapshot(
            market_type="perpetual", account_scope="test-account", instrument_id="BTC_USDT",
            environ=self._env(), timestamp_provider=lambda: 1700000000, opener=_Opener(),
        )
        body = snapshot.to_public_dict()
        self.assertEqual(body["instrument_count"], 1)
        self.assertEqual(body["position_count"], 0)


if __name__ == "__main__":
    unittest.main()
