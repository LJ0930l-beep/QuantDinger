from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
_MISSING = object()
_SANDBOXED_MODULES = (
    "app",
    "app.domain",
    "app.services",
    "app.services.live_trading",
    "app.domain.decimal_values",
    "app.domain.venue_order_contracts",
    "app.services.live_trading.base",
    "app.services.live_trading.symbols",
    "app.services.live_trading.binance",
)


@contextmanager
def _isolated_sys_modules(module_names: tuple[str, ...]):
    """Restore every touched module identity, even when subject loading fails."""

    original = {name: sys.modules.get(name, _MISSING) for name in module_names}
    try:
        yield
    finally:
        for name, previous in original.items():
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _register_package(name: str, location: Path) -> None:
    package = types.ModuleType(name)
    package.__path__ = [str(location)]
    sys.modules[name] = package


def _load_subject():
    """Bind the isolated class references, then immediately restore imports."""

    with _isolated_sys_modules(_SANDBOXED_MODULES):
        _register_package("app", BACKEND / "app")
        _register_package("app.domain", BACKEND / "app" / "domain")
        _register_package("app.services", BACKEND / "app" / "services")
        _register_package(
            "app.services.live_trading", BACKEND / "app" / "services" / "live_trading"
        )
        _load_module("app.domain.decimal_values", BACKEND / "app" / "domain" / "decimal_values.py")
        _load_module(
            "app.domain.venue_order_contracts",
            BACKEND / "app" / "domain" / "venue_order_contracts.py",
        )

        base = types.ModuleType("app.services.live_trading.base")

        class base_rest_client:
            pass

        class live_order_result:
            pass

        class live_trading_error(Exception):
            pass

        base.BaseRestClient = base_rest_client
        base.LiveOrderResult = live_order_result
        base.LiveTradingError = live_trading_error
        sys.modules[base.__name__] = base

        symbols = types.ModuleType("app.services.live_trading.symbols")
        symbols.to_binance_futures_symbol = lambda value: value
        sys.modules[symbols.__name__] = symbols
        binance = _load_module(
            "app.services.live_trading.binance",
            BACKEND / "app" / "services" / "live_trading" / "binance.py",
        )
        return binance.BinanceFuturesClient, live_trading_error


BinanceFuturesClient, LiveTradingError = _load_subject()


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

    def test_subject_loading_restores_preexisting_symbols_module_identity(self):
        name = "app.services.live_trading.symbols"
        original = sys.modules.get(name, _MISSING)
        real_symbols = types.ModuleType(name)
        real_symbols.to_coinbase_product_id = lambda value: value
        real_symbols.to_bitget_um_symbol = lambda value: value
        try:
            sys.modules[name] = real_symbols
            _load_subject()
            self.assertIs(sys.modules[name], real_symbols)
            self.assertIsNotNone(real_symbols.to_coinbase_product_id)
            self.assertIsNotNone(real_symbols.to_bitget_um_symbol)
        finally:
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def test_two_subject_loads_and_load_failure_leave_no_fake_modules(self):
        before = {name: sys.modules.get(name, _MISSING) for name in _SANDBOXED_MODULES}
        _load_subject()
        _load_subject()
        self.assertEqual(
            {name: sys.modules.get(name, _MISSING) for name in _SANDBOXED_MODULES},
            before,
        )
        with self.assertRaises(RuntimeError):
            with _isolated_sys_modules(_SANDBOXED_MODULES):
                sys.modules["app.services.live_trading.symbols"] = types.ModuleType(
                    "app.services.live_trading.symbols"
                )
                raise RuntimeError("injected load failure")
        self.assertEqual(
            {name: sys.modules.get(name, _MISSING) for name in _SANDBOXED_MODULES},
            before,
        )


if __name__ == "__main__":
    unittest.main()
