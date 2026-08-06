import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)


def load():
    names = [
        "app", "app.domain", "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_readonly_contracts", "app.domain.gate_read_formatters",
        "app.domain.gate_read_transport_contracts", "app.domain.gate_readonly_adapter_contracts",
        "app.domain.gate_market_read_contracts", "app.domain.gate_market_payload_contracts",
        "app.services", "app.services.gate_market_research_service",
    ]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(ROOT / "app" / "services")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain; sys.modules["app.services"] = services
        paths = {
            names[2]: ROOT / "app/domain/multi_asset_capability_contracts.py",
            names[3]: ROOT / "app/domain/gate_readonly_contracts.py",
            names[4]: ROOT / "app/domain/gate_read_formatters.py",
            names[5]: ROOT / "app/domain/gate_read_transport_contracts.py",
            names[6]: ROOT / "app/domain/gate_readonly_adapter_contracts.py",
            names[7]: ROOT / "app/domain/gate_market_read_contracts.py",
            names[8]: ROOT / "app/domain/gate_market_payload_contracts.py",
            names[10]: ROOT / "app/services/gate_market_research_service.py",
        }
        # The market formatter imports the market contract before the payload
        # formatter, while transport imports the error formatter.
        order = [names[2], names[7], names[3], names[4], names[5], names[6], names[8], names[10]]
        for name in order:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[10]], sys.modules[names[3]], sys.modules[names[5]], sys.modules[names[6]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, RO, TRANSPORT, ADAPTER = load()


def profile():
    return RO.GateReadCapabilityProfile(RO.GateEnvironment.TESTNET, RO.GateMarketType.SPOT, credential_ref="opaque-ref")


def payloads(request):
    if request.endpoint is TRANSPORT.GatePublicReadEndpoint.CANDLESTICKS:
        return TRANSPORT.GateReadResponse(200, [["1767225600", "1000", "101", "102", "99", "100", "10", True]])
    return TRANSPORT.GateReadResponse(200, {"id": 7, "current": 1767225720000, "update": 1767225719000, "bids": [["100", "1"]], "asks": [["101", "2"]]})


class GateMarketResearchServiceTests(unittest.TestCase):
    def test_composes_injected_reads_into_stable_typed_bundle(self):
        seen = []
        def transport(request):
            seen.append(request)
            return payloads(request)

        adapter = ADAPTER.GateReadonlyAdapter(profile(), transport)
        service = M.GateMarketResearchService(adapter, "fixture", "evidence")
        bundle = service.read_market_evidence(
            "BTC_USDT", observed_at=UTC, snapshot_id="snapshot-1", rule_version="rules-1",
        )
        self.assertEqual(len(bundle.candles), 1)
        self.assertEqual(bundle.order_book.sequence, 7)
        self.assertEqual(bundle.order_book.depth_limit, 20)
        self.assertEqual(bundle.bundle_fingerprint, service.read_market_evidence(
            "BTC_USDT", observed_at=UTC, snapshot_id="snapshot-1", rule_version="rules-1",
        ).bundle_fingerprint)
        self.assertEqual([item.path for item in seen], ["/spot/candlesticks", "/spot/order_book"] * 2)

    def test_invalid_or_error_payload_is_typed_and_does_not_leak(self):
        adapter = ADAPTER.GateReadonlyAdapter(
            profile(), lambda request: TRANSPORT.GateReadResponse(200, {"secret": "never-returned"}),
        )
        with self.assertRaises(M.GateMarketResearchServiceError) as caught:
            M.GateMarketResearchService(adapter, "fixture", "evidence").read_market_evidence(
                "BTC_USDT", observed_at=UTC, snapshot_id="snapshot-1", rule_version="rules-1",
            )
        self.assertNotIn("never-returned", str(caught.exception))

    def test_live_or_nonzero_offset_is_rejected_before_read(self):
        with self.assertRaises(M.GateMarketResearchServiceError):
            M.GateMarketResearchService(object(), "fixture", "evidence")
        adapter = ADAPTER.GateReadonlyAdapter(profile(), payloads)
        with self.assertRaises(M.GateMarketResearchServiceError):
            M.GateMarketResearchService(adapter, "fixture", "evidence").read_market_evidence(
                "BTC_USDT", observed_at=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
                snapshot_id="snapshot-1", rule_version="rules-1",
            )

    def test_direct_bundle_rejects_empty_duplicate_or_gapped_candle_evidence(self):
        candle_type = M.GateMarketEvidenceBundle.__post_init__.__globals__["GateCandleFact"]
        book_type = M.GateMarketEvidenceBundle.__post_init__.__globals__["GateOrderBookSnapshot"]
        level_type = book_type.__post_init__.__globals__["GateOrderBookLevel"]
        market_type = candle_type.__post_init__.__globals__["AssetMarketType"].SPOT

        def candle(sequence, *, opened, source_event_id=None):
            return candle_type(
                market_type=market_type, instrument_id="BTC_USDT", interval="1m",
                open_time=opened, close_time=opened + timedelta(minutes=1),
                open_price=Decimal("100"), high_price=Decimal("102"), low_price=Decimal("99"),
                close_price=Decimal("101"), volume=Decimal("1"), occurred_at=opened,
                observed_at=UTC, sequence=sequence, source_event_id=source_event_id or f"candle:{sequence}",
                snapshot_id="snapshot-1", rule_version="rules-1", evidence_hash=f"hash:{sequence}",
            )

        book = book_type(
            market_type=market_type, instrument_id="BTC_USDT",
            bids=(level_type(Decimal("100"), Decimal("1")),),
            asks=(level_type(Decimal("101"), Decimal("1")),),
            occurred_at=UTC - timedelta(seconds=1), observed_at=UTC, sequence=9,
            source_event_id="book:9", snapshot_id="snapshot-1", rule_version="rules-1", evidence_hash="book-hash",
        )
        args = (market_type, "BTC_USDT", "1m")
        with self.assertRaises(M.GateMarketResearchServiceError):
            M.GateMarketEvidenceBundle(*args, (), book, UTC, "snapshot-1", "rules-1")
        first = candle(1, opened=UTC - timedelta(minutes=2))
        with self.assertRaises(M.GateMarketResearchServiceError):
            M.GateMarketEvidenceBundle(*args, (first, candle(2, opened=UTC)), book, UTC, "snapshot-1", "rules-1")
        with self.assertRaises(M.GateMarketResearchServiceError):
            M.GateMarketEvidenceBundle(*args, (first, candle(2, opened=UTC - timedelta(minutes=1), source_event_id="candle:1")), book, UTC, "snapshot-1", "rules-1")


if __name__ == "__main__":
    unittest.main()
