"""Offline tests for the concrete Gate public-read transport."""

from __future__ import annotations

import io
import os
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


app_module = types.ModuleType("app")
app_module.__path__ = [str(ROOT / "app")]
domain_module = types.ModuleType("app.domain")
domain_module.__path__ = [str(ROOT / "app" / "domain")]
services_module = types.ModuleType("app.services")
services_module.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app", app_module)
sys.modules.setdefault("app.domain", domain_module)
sys.modules.setdefault("app.services", services_module)

from app.domain.gate_read_formatters import GateReadErrorKind
from app.domain.gate_read_transport_contracts import GatePublicReadEndpoint, GateReadRequest
from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, GateReadCapabilityProfile
from app.services.gate_read_http_transport import (
    GateReadHttpTransport,
    GateReadHttpTransportError,
    _configured_proxy_url,
)


class _Response:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = io.BytesIO(body)
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        return self._body.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


def _profile() -> GateReadCapabilityProfile:
    return GateReadCapabilityProfile(
        GateEnvironment.TESTNET,
        GateMarketType.SPOT,
        credential_ref="fixture-only",
    )


class GateReadHttpTransportTests(unittest.TestCase):
    def test_explicit_http_proxy_is_used_as_application_egress_setting(self):
        previous = os.environ.get("PROXY_URL")
        try:
            os.environ["PROXY_URL"] = "http://127.0.0.1:8080"
            self.assertEqual(_configured_proxy_url(), "http://127.0.0.1:8080")
        finally:
            if previous is None:
                os.environ.pop("PROXY_URL", None)
            else:
                os.environ["PROXY_URL"] = previous

    def test_non_http_proxy_fails_closed(self):
        previous = os.environ.get("PROXY_URL")
        try:
            os.environ["PROXY_URL"] = "socks5h://127.0.0.1:1080"
            with self.assertRaises(GateReadHttpTransportError):
                _configured_proxy_url()
        finally:
            if previous is None:
                os.environ.pop("PROXY_URL", None)
            else:
                os.environ["PROXY_URL"] = previous

    def test_builds_https_get_and_parses_json(self):
        calls = []

        def opener(request, *, timeout):
            calls.append((request.full_url, request.get_method(), request.headers, timeout))
            return _Response(200, b'{"currency_pair":"BTC_USDT"}')

        transport = GateReadHttpTransport(_profile(), opener=opener)
        response = transport(GateReadRequest(GateMarketType.SPOT, GatePublicReadEndpoint.TICKERS, "BTC_USDT"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["currency_pair"], "BTC_USDT")
        self.assertEqual(calls[0][1], "GET")
        self.assertIn("/api/v4/spot/tickers?currency_pair=BTC_USDT", calls[0][0])
        self.assertEqual(calls[0][3], 10)

    def test_non_200_is_typed_gate_response(self):
        def opener(_request, *, timeout):
            return _Response(429, b'{"label":"TOO_FAST"}')

        response = GateReadHttpTransport(_profile(), opener=opener)(
            GateReadRequest(GateMarketType.SPOT, GatePublicReadEndpoint.TICKERS, "BTC_USDT")
        )
        self.assertEqual(response.error_kind, GateReadErrorKind.RATE_LIMIT)

    def test_invalid_success_payload_fails_closed(self):
        def opener(_request, *, timeout):
            return _Response(200, b'not-json')

        with self.assertRaises(GateReadHttpTransportError):
            GateReadHttpTransport(_profile(), opener=opener)(
                GateReadRequest(GateMarketType.SPOT, GatePublicReadEndpoint.TICKERS, "BTC_USDT")
            )

    def test_transport_failure_does_not_leak_raw_exception(self):
        def opener(_request, *, timeout):
            raise OSError("fixture transport failed")

        with self.assertRaises(GateReadHttpTransportError) as ctx:
            GateReadHttpTransport(_profile(), opener=opener)(
                GateReadRequest(GateMarketType.SPOT, GatePublicReadEndpoint.TICKERS, "BTC_USDT")
            )
        self.assertEqual(str(ctx.exception), "Gate public GET failed")


if __name__ == "__main__":
    unittest.main()
