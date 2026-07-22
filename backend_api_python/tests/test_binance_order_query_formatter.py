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
domain = _load_module("app.domain.venue_order_contracts", BACKEND / "app" / "domain" / "venue_order_contracts.py")
formatter = _load_module(
    "app.services.live_trading.order_query_formatter",
    BACKEND / "app" / "services" / "live_trading" / "order_query_formatter.py",
)

OrderQueryReference = domain.OrderQueryReference
OrderQueryRequest = domain.OrderQueryRequest
OrderQueryStatus = domain.OrderQueryStatus
format_binance_order_query = formatter.format_binance_order_query
format_binance_fill_identity = formatter.format_binance_fill_identity
classify_binance_query_http_failure = formatter.classify_binance_query_http_failure
VenueQueryFailureKind = domain.VenueQueryFailureKind


class BinanceOrderQueryFormatterTests(unittest.TestCase):
    def _request(self, *, reference=OrderQueryReference.EXCHANGE_ORDER_ID):
        if reference is OrderQueryReference.EXCHANGE_ORDER_ID:
            return OrderQueryRequest(reference, "binance", "swap", "credential-scope-a", "BTCUSDT", exchange_order_id="123")
        return OrderQueryRequest(reference, "binance", "swap", "credential-scope-a", "BTCUSDT", client_order_id="cid-1")

    def test_formats_mocked_exchange_order_lookup(self):
        result = format_binance_order_query(
            self._request(),
            {"orderId": "123", "clientOrderId": "cid-1", "symbol": "BTCUSDT", "status": "NEW"},
            response_account_scope="credential-scope-a",
        )
        self.assertEqual(result.status, OrderQueryStatus.FOUND)
        self.assertEqual(result.normalized_state, "SUBMITTED")
        self.assertEqual(result.raw_state, "NEW")

    def test_formats_mocked_client_order_lookup(self):
        result = format_binance_order_query(
            self._request(reference=OrderQueryReference.CLIENT_ORDER_ID),
            {"orderId": "123", "clientOrderId": "cid-1", "symbol": "BTCUSDT", "status": "FILLED"},
            response_account_scope="credential-scope-a",
        )
        self.assertEqual(result.status, OrderQueryStatus.FOUND)
        self.assertEqual(result.exchange_order_id, "123")

    def test_missing_or_unknown_payload_is_never_not_found(self):
        request = self._request()
        for payload in (None, {}, {"orderId": "123", "symbol": "BTCUSDT", "status": "MYSTERY"}):
            with self.subTest(payload=payload):
                self.assertEqual(
                    format_binance_order_query(request, payload, response_account_scope="credential-scope-a").status,
                    OrderQueryStatus.INVALID_RESPONSE,
                )

    def test_timeout_rate_limit_server_and_auth_are_not_not_found(self):
        self.assertEqual(classify_binance_query_http_failure(timed_out=True), VenueQueryFailureKind.TIMEOUT)
        self.assertEqual(classify_binance_query_http_failure(status_code=429), VenueQueryFailureKind.TIMEOUT)
        self.assertEqual(classify_binance_query_http_failure(status_code=503), VenueQueryFailureKind.TIMEOUT)
        self.assertEqual(classify_binance_query_http_failure(status_code=401), VenueQueryFailureKind.AUTH_OR_PERMISSION)
        self.assertEqual(classify_binance_query_http_failure(status_code=403), VenueQueryFailureKind.AUTH_OR_PERMISSION)
        self.assertEqual(classify_binance_query_http_failure(status_code=400), VenueQueryFailureKind.INVALID_RESPONSE)

    def test_scope_or_identity_mismatch_fails_closed(self):
        request = self._request()
        self.assertEqual(
            format_binance_order_query(
                request,
                {"orderId": "other", "symbol": "BTCUSDT", "status": "NEW"},
                response_account_scope="credential-scope-a",
            ).status,
            OrderQueryStatus.INVALID_RESPONSE,
        )
        self.assertEqual(
            format_binance_order_query(
                request,
                {"orderId": "123", "symbol": "ETHUSDT", "status": "NEW"},
                response_account_scope="credential-scope-a",
            ).status,
            OrderQueryStatus.INVALID_RESPONSE,
        )
        self.assertEqual(
            format_binance_order_query(
                request,
                {"orderId": "123", "symbol": "BTCUSDT", "status": "NEW"},
                response_account_scope="credential-scope-b",
            ).status,
            OrderQueryStatus.INVALID_RESPONSE,
        )

    def test_extracts_stable_fill_id_and_preserves_fee_asset(self):
        scope = domain.VenueOrderScope("binance", "spot", "credential-scope-a", "BTCUSDT", "123")
        fill = format_binance_fill_identity(
            scope,
            {
                "id": "trade-44",
                "orderId": "123",
                "symbol": "BTCUSDT",
                "qty": "0.25",
                "price": "100000",
                "commission": "0.01",
                "commissionAsset": "BNB",
            },
        )
        self.assertEqual(fill.venue_fill_id, "trade-44")
        self.assertEqual(fill.fees[0].asset, "BNB")
        with self.assertRaisesRegex(domain.VenueContractError, "stable venue_fill_id"):
            format_binance_fill_identity(scope, {"orderId": "123", "symbol": "BTCUSDT", "qty": "0.25", "price": "100000", "commission": "0.01", "commissionAsset": "BNB"})
        with self.assertRaisesRegex(domain.VenueContractError, "fill scope mismatch"):
            format_binance_fill_identity(scope, {"id": "trade-44", "orderId": "other", "symbol": "BTCUSDT", "qty": "0.25", "price": "100000", "commission": "0.01", "commissionAsset": "BNB"})


if __name__ == "__main__":
    unittest.main()
