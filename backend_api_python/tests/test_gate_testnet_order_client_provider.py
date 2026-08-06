from __future__ import annotations

import importlib.util
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
        "app.domain.multi_asset_capability_contracts", "app.domain.gate_vertical_read_contracts",
        "app.domain.gate_testnet_execution_contracts", "app.domain.gate_readonly_contracts",
        "app.services.gate_private_read_client", "app.services.gate_testnet_order_client",
        "app.services.gate_testnet_order_http_transport", "app.services.gate_testnet_credential_provider",
        "app.services.gate_testnet_order_client_provider",
    )
    missing = object()
    previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        _load(names[3], ROOT / "app" / "domain" / "decimal_values.py")
        multi = _load(names[4], ROOT / "app" / "domain" / "multi_asset_capability_contracts.py")
        _load(names[5], ROOT / "app" / "domain" / "gate_vertical_read_contracts.py")
        _load(names[6], ROOT / "app" / "domain" / "gate_testnet_execution_contracts.py")
        _load(names[7], ROOT / "app" / "domain" / "gate_readonly_contracts.py")
        _load(names[8], ROOT / "app" / "services" / "gate_private_read_client.py")
        order = _load(names[9], ROOT / "app" / "services" / "gate_testnet_order_client.py")
        _load(names[10], ROOT / "app" / "services" / "gate_testnet_order_http_transport.py")
        _load(names[11], ROOT / "app" / "services" / "gate_testnet_credential_provider.py")
        provider = _load(names[12], ROOT / "app" / "services" / "gate_testnet_order_client_provider.py")
        return multi, provider, order
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


MULTI, PROVIDER, ORDER = _modules()


class GateTestnetOrderClientProviderTests(unittest.TestCase):
    def _env(self, **changes):
        values = {
            "GATE_TESTNET_API_KEY": "test-key",
            "GATE_TESTNET_API_SECRET": "test-secret",
            "AGENT_LIVE_TRADING_ENABLED": "false",
        }
        values.update(changes)
        return values

    def test_reads_build_get_only_client_without_write_opt_in(self):
        client = PROVIDER.build_gate_testnet_order_client(
            market_type=MULTI.AssetMarketType.SPOT,
            account_scope="test-account",
            timestamp_provider=lambda: 1,
            client_order_id_validator=lambda value: value,
            environ=self._env(),
        )
        self.assertEqual(client.credential.environment.value, "testnet")
        with self.assertRaises(ORDER.GateTestnetOrderCapabilityError):
            PROVIDER.build_gate_testnet_order_client(
                market_type=MULTI.AssetMarketType.SPOT,
                account_scope="test-account",
                timestamp_provider=lambda: 1,
                client_order_id_validator=lambda value: value,
                environ=self._env(),
                allow_writes=True,
            )

    def test_writes_require_explicit_testnet_flag(self):
        client = PROVIDER.build_gate_testnet_order_client(
            market_type=MULTI.AssetMarketType.PERPETUAL,
            account_scope="test-account",
            timestamp_provider=lambda: 1,
            client_order_id_validator=lambda value: value,
            environ=self._env(GATE_TESTNET_WRITE_ENABLED="1"),
            allow_writes=True,
        )
        self.assertTrue(client.transport.allow_testnet_writes)

    def test_live_flag_is_rejected_without_leaking_credentials(self):
        with self.assertRaises(PROVIDER.GateTestnetOrderClientProviderError) as ctx:
            PROVIDER.build_gate_testnet_order_client(
                market_type=MULTI.AssetMarketType.SPOT,
                account_scope="test-account",
                timestamp_provider=lambda: 1,
                client_order_id_validator=lambda value: value,
                environ=self._env(AGENT_LIVE_TRADING_ENABLED="true"),
            )
        self.assertNotIn("test-secret", str(ctx.exception))

    def test_resolved_gate_testnet_config_builds_write_client(self):
        client = PROVIDER.build_gate_testnet_order_client_from_config(
            {
                "exchange_id": "gate",
                "environment": "testnet",
                "api_key": "test-key",
                "secret_key": "test-secret",
            },
            market_type=MULTI.AssetMarketType.SPOT,
            account_scope="test-account",
            timestamp_provider=lambda: 1,
            client_order_id_validator=lambda value: value,
            environ=self._env(GATE_TESTNET_WRITE_ENABLED="1"),
            allow_writes=True,
        )
        self.assertTrue(client.transport.allow_testnet_writes)

    def test_resolved_live_or_other_exchange_config_fails_closed(self):
        for config in (
            {"exchange_id": "gate", "environment": "live", "api_key": "test-key", "secret_key": "test-secret"},
            {"exchange_id": "binance", "environment": "testnet", "api_key": "test-key", "secret_key": "test-secret"},
        ):
            with self.assertRaises(ORDER.GateTestnetOrderCapabilityError):
                PROVIDER.build_gate_testnet_order_client_from_config(
                    config,
                    market_type=MULTI.AssetMarketType.SPOT,
                    account_scope="test-account",
                    timestamp_provider=lambda: 1,
                    client_order_id_validator=lambda value: value,
                    environ=self._env(GATE_TESTNET_WRITE_ENABLED="1"),
                    allow_writes=True,
                )


if __name__ == "__main__":
    unittest.main()
