"""Focused safety tests for the opt-in Gate TestNet HTTP transport."""

from __future__ import annotations

import json
import unittest

from app.services.gate_testnet_order_http_transport import (
    GateTestnetOrderCapabilityError,
    GateTestnetOrderHttpTransport,
)


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


class GateTestnetOrderHttpTransportTests(unittest.TestCase):
    def test_disabled_transport_fails_closed_without_opener(self):
        transport = GateTestnetOrderHttpTransport(allow_testnet_writes=False)
        with self.assertRaises(GateTestnetOrderCapabilityError):
            transport.request("POST", "/api/v4/spot/orders", "", "{}", {})

    def test_enabled_transport_uses_only_official_testnet_host(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["method"] = request.method
            seen["timeout"] = timeout
            return _Response({"id": "order-1"})

        transport = GateTestnetOrderHttpTransport(allow_testnet_writes=True, opener=opener, timeout_seconds=7)
        status, payload = transport.request(
            "POST", "/api/v4/spot/orders", "", "{}", {"KEY": "opaque", "SIGN": "opaque"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["id"], "order-1")
        self.assertEqual(seen["url"], "https://api-testnet.gateapi.io/api/v4/spot/orders")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
