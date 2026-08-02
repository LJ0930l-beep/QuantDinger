"""Fixture-backed Gate TestNet market session tests (no network)."""

import importlib.util
import sys
import unittest
from datetime import datetime, timezone
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
        "app.services.gate_testnet_readiness_service", "app.services.gate_testnet_market_session_service",
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
            names[11]: ROOT / "app/services/gate_testnet_readiness_service.py",
            names[12]: ROOT / "app/services/gate_testnet_market_session_service.py",
        }
        order = [names[2], names[7], names[3], names[4], names[5], names[6], names[8], names[10], names[11], names[12]]
        for name in order:
            spec = importlib.util.spec_from_file_location(name, paths[name])
            module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return tuple(sys.modules[name] for name in (names[3], names[5], names[6], names[10], names[12]))
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


RO, TRANSPORT, ADAPTER, MARKET, SESSION = load()


def profile():
    return RO.GateReadCapabilityProfile(RO.GateEnvironment.TESTNET, RO.GateMarketType.SPOT, credential_ref="fixture-ref")


def payloads(request):
    if request.endpoint is TRANSPORT.GatePublicReadEndpoint.CANDLESTICKS:
        return TRANSPORT.GateReadResponse(200, [["1767225600", "1000", "101", "102", "99", "100", "10", True]])
    return TRANSPORT.GateReadResponse(200, {"id": 7, "current": 1767225720000, "update": 1767225719000, "bids": [["100", "1"]], "asks": [["101", "2"]]})


class GateTestnetMarketSessionTests(unittest.TestCase):
    def test_fixture_session_is_ready_deterministic_and_non_live(self):
        adapter = ADAPTER.GateReadonlyAdapter(profile(), payloads)
        service = SESSION.GateTestnetMarketSessionService(MARKET.GateMarketResearchService(adapter, "fixture", "evidence"))
        request = SESSION.GateTestnetMarketSessionRequest("BTC_USDT", UTC, "snapshot-1", "rules-1")
        first = service.read(request)
        second = service.read(request)
        self.assertEqual(first.session_fingerprint, second.session_fingerprint)
        self.assertEqual(first.readiness.writes_enabled, False)
        self.assertEqual(first.readiness.live_enabled, False)
        self.assertEqual(first.evidence.instrument_id, "BTC_USDT")

    def test_invalid_request_fails_before_transport(self):
        adapter = ADAPTER.GateReadonlyAdapter(profile(), payloads)
        service = SESSION.GateTestnetMarketSessionService(MARKET.GateMarketResearchService(adapter, "fixture", "evidence"))
        with self.assertRaises(SESSION.GateTestnetMarketSessionError):
            SESSION.GateTestnetMarketSessionRequest("BTC USDT", UTC, "snapshot-1", "rules-1")


if __name__ == "__main__":
    unittest.main()
