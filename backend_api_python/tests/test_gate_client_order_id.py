from __future__ import annotations

import unittest

from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.gate import GateSpotClient


class GateClientOrderIdTests(unittest.TestCase):
    def _client(self) -> GateSpotClient:
        return GateSpotClient(api_key="key", secret_key="secret")

    def test_valid_id_is_prefixed_without_rewriting(self):
        client = self._client()
        self.assertEqual(client._format_text("order-001.A"), "t-order-001.A")
        self.assertEqual(client._format_text("t-already-valid"), "t-already-valid")

    def test_overlong_id_is_rejected_not_truncated(self):
        with self.assertRaises(LiveTradingError):
            self._client()._format_text("x" * 29)

    def test_whitespace_unicode_and_unsupported_characters_are_rejected(self):
        client = self._client()
        for value in (" order", "order ", "订单", "order:id"):
            with self.subTest(value=value):
                with self.assertRaises(LiveTradingError):
                    client._format_text(value)


if __name__ == "__main__":
    unittest.main()
