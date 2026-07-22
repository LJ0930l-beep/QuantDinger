from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_package(name: str, location: Path) -> None:
    package = types.ModuleType(name)
    package.__path__ = [str(location)]
    sys.modules.setdefault(name, package)


_ensure_package("app", BACKEND / "app")
_ensure_package("app.domain", BACKEND / "app" / "domain")
_ensure_package("app.services", BACKEND / "app" / "services")
_ensure_package("app.services.live_trading", BACKEND / "app" / "services" / "live_trading")
_load_module("app.domain.decimal_values", BACKEND / "app" / "domain" / "decimal_values.py")
_load_module("app.domain.venue_order_contracts", BACKEND / "app" / "domain" / "venue_order_contracts.py")

base = types.ModuleType("app.services.live_trading.base")


class _BaseRestClient:
    pass


class _LiveOrderResult:
    pass


class _LiveTradingError(Exception):
    pass


base.BaseRestClient = _BaseRestClient
base.LiveOrderResult = _LiveOrderResult
base.LiveTradingError = _LiveTradingError
sys.modules[base.__name__] = base
symbols = types.ModuleType("app.services.live_trading.symbols")
symbols.to_binance_futures_symbol = lambda value: value
sys.modules[symbols.__name__] = symbols
binance = _load_module(
    "app.services.live_trading.binance",
    BACKEND / "app" / "services" / "live_trading" / "binance.py",
)

BinanceFuturesClient = binance.BinanceFuturesClient
LiveTradingError = base.LiveTradingError


class BinanceFuturesClientIdContractTests(unittest.TestCase):
    def _client_without_network(self, broker_id="HBpUbQjT"):
        return types.SimpleNamespace(broker_id=broker_id)

    def test_existing_broker_prefixed_id_is_preserved(self):
        client = self._client_without_network()
        client_id = "x-HBpUbQjTattempt-1"
        self.assertEqual(BinanceFuturesClient._format_client_order_id(client, client_id), client_id)

    def test_unprefixed_id_gets_broker_prefix_and_remains_valid(self):
        client = self._client_without_network()
        self.assertEqual(BinanceFuturesClient._format_client_order_id(client, "attempt-1"), "x-HBpUbQjTattempt-1")

    def test_overlong_or_whitespace_id_is_rejected_not_truncated(self):
        client = self._client_without_network()
        with self.assertRaisesRegex(LiveTradingError, "violates"):
            BinanceFuturesClient._format_client_order_id(client, "a" * 30)
        with self.assertRaisesRegex(LiveTradingError, "violates"):
            BinanceFuturesClient._format_client_order_id(client, " attempt")


if __name__ == "__main__":
    unittest.main()
