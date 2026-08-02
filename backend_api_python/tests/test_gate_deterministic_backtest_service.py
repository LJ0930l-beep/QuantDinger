from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "app" / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


helper_spec = importlib.util.spec_from_file_location("backtest_service_fixture", ROOT / "tests" / "test_deterministic_backtest_service.py")
assert helper_spec and helper_spec.loader
helper = importlib.util.module_from_spec(helper_spec)
sys.modules[helper_spec.name] = helper
helper_spec.loader.exec_module(helper)

CAP = load("app.domain.multi_asset_capability_contracts", "domain/multi_asset_capability_contracts.py")
GATE_RO = load("app.domain.gate_readonly_contracts", "domain/gate_readonly_contracts.py")
GATE_FMT = load("app.domain.gate_read_formatters", "domain/gate_read_formatters.py")
GATE_TRANSPORT = load("app.domain.gate_read_transport_contracts", "domain/gate_read_transport_contracts.py")
GATE_ADAPTER = load("app.domain.gate_readonly_adapter_contracts", "domain/gate_readonly_adapter_contracts.py")
GATE_MARKET = load("app.domain.gate_market_read_contracts", "domain/gate_market_read_contracts.py")
GATE_PAYLOAD = load("app.domain.gate_market_payload_contracts", "domain/gate_market_payload_contracts.py")
load("app.domain.gate_testnet_readiness_contracts", "domain/gate_testnet_readiness_contracts.py")
GATE_MARKET_SERVICE = load("app.services.gate_market_research_service", "services/gate_market_research_service.py")
GATE_READY = load("app.services.gate_testnet_readiness_service", "services/gate_testnet_readiness_service.py")
SESSION = load("app.services.gate_testnet_market_session_service", "services/gate_testnet_market_session_service.py")
SERVICE = load("app.services.gate_deterministic_backtest_service", "services/gate_deterministic_backtest_service.py")
load("app.domain.paper_shadow_contracts", "domain/paper_shadow_contracts.py")
load("app.domain.portfolio_risk_contracts", "domain/portfolio_risk_contracts.py")
load("app.services.paper_shadow_service", "services/paper_shadow_service.py")
load("app.services.portfolio_risk_service", "services/portfolio_risk_service.py")
load("app.services.research_pipeline", "services/research_pipeline.py")
ORCHESTRATOR = load("app.services.gate_non_live_research_orchestrator", "services/gate_non_live_research_orchestrator.py")
PAPER = sys.modules["app.domain.paper_shadow_contracts"]
RISK = sys.modules["app.domain.portfolio_risk_contracts"]

START = datetime(2026, 1, 1, 0, 10, tzinfo=timezone.utc)


def profile():
    return GATE_RO.GateReadCapabilityProfile(GATE_RO.GateEnvironment.TESTNET, GATE_RO.GateMarketType.SPOT, credential_ref="fixture-ref")


def payloads(request):
    if request.endpoint is GATE_TRANSPORT.GatePublicReadEndpoint.CANDLESTICKS:
        return GATE_TRANSPORT.GateReadResponse(200, [
            ["1767225600", "100", "101", "102", "99", "100", "10", True],
            ["1767225660", "100", "101", "102", "99", "100", "10", True],
            ["1767225720", "100", "101", "102", "99", "100", "10", True],
            ["1767225780", "100", "101", "103", "98", "101", "10", True],
            ["1767225840", "101", "100", "102", "99", "100", "10", True],
        ])
    return GATE_TRANSPORT.GateReadResponse(200, {"id": 7, "current": 1767225720000, "update": 1767225719000, "bids": [["100", "1"]], "asks": [["101", "2"]]})


class GateDeterministicBacktestServiceTests(unittest.TestCase):
    def test_gate_session_produces_replayable_strategy_trace(self):
        adapter = GATE_ADAPTER.GateReadonlyAdapter(profile(), payloads)
        session = SESSION.GateTestnetMarketSessionService(
            GATE_MARKET_SERVICE.GateMarketResearchService(adapter, "fixture", "evidence")
        ).read(SESSION.GateTestnetMarketSessionRequest("BTC_USDT", START, "dataset-1", "rules-v1"))
        result = SERVICE.GateDeterministicBacktestService().run(
            session,
            helper._run(),
            helper._strategy(),
            order_quantity=helper.Decimal("1"),
        )
        self.assertEqual(result.dataset.dataset_snapshot_id, "dataset-1")
        self.assertEqual(result.result_fingerprint, SERVICE.GateDeterministicBacktestResult(
            session, result.dataset, result.strategy_backtest
        ).result_fingerprint)
        self.assertFalse(result.strategy_backtest.to_public_dict()["live_enabled"])

    def test_run_rejects_snapshot_mismatch_before_read(self):
        with self.assertRaises(SERVICE.GateDeterministicBacktestError):
            SERVICE.GateDeterministicBacktestService().run(None, helper._run(), helper._strategy(), order_quantity=helper.Decimal("1"))

    def test_orchestrator_uses_one_dataset_for_pipeline_and_backtest(self):
        adapter = GATE_ADAPTER.GateReadonlyAdapter(profile(), payloads)
        session_service = SESSION.GateTestnetMarketSessionService(
            GATE_MARKET_SERVICE.GateMarketResearchService(adapter, "fixture", "evidence")
        )
        strategy = helper._strategy()
        sizing = RISK.PositionSizingRequest(
            "request-1", "BTC_USDT", helper.Decimal("100"), helper.Decimal("1"),
            helper.Decimal("1000"), helper.Decimal("1000"), helper.Decimal("2"),
            helper.Decimal("0.5"), START,
        )
        paper_run = PAPER.PaperShadowRunFacts(
            "paper-run", PAPER.SimulationMode.SHADOW, "dataset-1", "strategy-1",
            "risk-1", "tolerance-1", START,
        )
        result = ORCHESTRATOR.GateNonLiveResearchOrchestrator(session_service).run(
            SESSION.GateTestnetMarketSessionRequest("BTC_USDT", START, "dataset-1", "rules-v1"),
            strategy, sizing, paper_run, helper._run(), signal_id="signal-1",
            request_fingerprint="request-1", decided_at=START, order_quantity=helper.Decimal("1"),
        )
        self.assertEqual(result.dataset.dataset_fingerprint, result.deterministic_backtest.dataset.dataset_fingerprint)
        self.assertEqual(result.pipeline.simulation.run_id, "paper-run")
        self.assertFalse(result.to_public_dict()["live_enabled"])


if __name__ == "__main__":
    unittest.main()
