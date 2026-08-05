import importlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]


def _load():
    names = ("app", "app.domain", "app.services")
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        return (
            importlib.import_module("app.domain.gate_market_read_contracts"),
            importlib.import_module("app.domain.multi_asset_capability_contracts"),
            importlib.import_module("app.services.gate_market_research_service"),
            importlib.import_module("app.services.gate_public_market_service"),
        )
    finally:
        for name in reversed(names):
            if old[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old[name]


M, C, R, S = _load()
UTC = timezone.utc
NOW = datetime(2026, 1, 1, 0, 2, tzinfo=UTC)


def bundle():
    candle = M.GateCandleFact(
        market_type=C.AssetMarketType.SPOT, instrument_id="BTC_USDT", interval="1m",
        open_time=NOW - timedelta(minutes=2), close_time=NOW - timedelta(minutes=1),
        open_price=Decimal("100"), high_price=Decimal("102"), low_price=Decimal("99"),
        close_price=Decimal("101"), volume=Decimal("2"), occurred_at=NOW - timedelta(minutes=2),
        observed_at=NOW, sequence=1, source_event_id="fixture:1", snapshot_id="snap-1",
        rule_version="rules-v1", evidence_hash="fixture-hash-1",
    )
    book = M.GateOrderBookSnapshot(
        market_type=C.AssetMarketType.SPOT, instrument_id="BTC_USDT",
        bids=(M.GateOrderBookLevel(Decimal("100"), Decimal("1")),),
        asks=(M.GateOrderBookLevel(Decimal("101"), Decimal("1")),),
        occurred_at=NOW - timedelta(seconds=1), observed_at=NOW, sequence=2,
        source_event_id="fixture:2", snapshot_id="snap-1", rule_version="rules-v1",
        evidence_hash="fixture-hash-2",
    )
    return R.GateMarketEvidenceBundle(
        C.AssetMarketType.SPOT, "BTC_USDT", "1m", (candle,), book, NOW, "snap-1", "rules-v1"
    )


class GatePublicMarketServiceTests(unittest.TestCase):
    def test_disabled_provider_is_unavailable_and_live_is_false(self):
        status, body = S.GatePublicMarketReadService().read_response(
            instrument_id="BTC_USDT", market_type=S.GateMarketType.SPOT, observed_at=NOW
        )
        self.assertEqual(status, 503)
        self.assertEqual(body["reason"], "public_market_read_disabled")
        self.assertFalse(body["live_enabled"])

    def test_provider_returns_decimal_safe_market_evidence(self):
        value = bundle()
        service = S.GatePublicMarketReadService(lambda *args: value)
        status, body = service.read_response(
            instrument_id="BTC_USDT", market_type=S.GateMarketType.SPOT, observed_at=NOW
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["candles"][0]["close"], "101")
        self.assertEqual(body["order_book"]["bids"], [["100", "1"]])
        self.assertEqual(body["bundle_fingerprint"], value.bundle_fingerprint)
        self.assertTrue(body["network_access"])
        self.assertFalse(body["live_enabled"])

    def test_invalid_provider_result_fails_closed(self):
        service = S.GatePublicMarketReadService(lambda *args: object())
        with self.assertRaises(S.GatePublicMarketServiceError):
            service.read_response(
                instrument_id="BTC_USDT", market_type=S.GateMarketType.SPOT, observed_at=NOW
            )

    def test_invalid_scope_is_rejected_before_provider(self):
        called = []
        service = S.GatePublicMarketReadService(lambda *args: called.append(args))
        with self.assertRaises(S.GatePublicMarketServiceError):
            service.read_response(instrument_id="BTC USDT", market_type=S.GateMarketType.SPOT, observed_at=NOW)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
