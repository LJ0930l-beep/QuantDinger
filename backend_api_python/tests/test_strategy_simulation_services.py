"""Pure orchestration tests for strategy, Paper/Shadow, and portfolio risk."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if "app" not in sys.modules:
    app = types.ModuleType("app")
    app.__path__ = [str(ROOT / "app")]
    sys.modules["app"] = app
if "app.domain" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "app.domain", ROOT / "app" / "domain" / "__init__.py",
        submodule_search_locations=[str(ROOT / "app" / "domain")],
    )
    domain = importlib.util.module_from_spec(spec)
    sys.modules["app.domain"] = domain
    spec.loader.exec_module(domain)

from app.domain.deterministic_backtest_contracts import (  # noqa: E402
    BacktestBar,
    BacktestSide,
)
from app.domain.gate_market_payload_contracts import normalize_gate_candles  # noqa: E402
from app.domain.gate_backtest_dataset_contracts import build_gate_backtest_dataset  # noqa: E402
from app.domain.multi_asset_capability_contracts import AssetMarketType  # noqa: E402
from app.domain.paper_shadow_contracts import PaperShadowRunFacts, SimulationMode  # noqa: E402
from app.domain.portfolio_risk_contracts import (  # noqa: E402
    CooldownFact,
    CooldownState,
    PositionSizingRequest,
)
from app.domain.strategy_library_contracts import (  # noqa: E402
    StrategyDefinition,
    StrategyFamily,
    StrategyParameterFact,
)
from app.domain.paper_shadow_reducer_contracts import SimulationRecordDisposition  # noqa: E402
from app.services.paper_shadow_service import PaperShadowCandidate, PaperShadowService  # noqa: E402
from app.services.portfolio_risk_service import PortfolioRiskService  # noqa: E402
from app.services.strategy_factory import StrategyFactory, StrategyFactoryError  # noqa: E402
from app.services.research_pipeline import ResearchPipeline  # noqa: E402
from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, GateReadCapabilityProfile  # noqa: E402
from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter  # noqa: E402
from app.domain.gate_read_transport_contracts import GatePublicReadEndpoint, GateReadResponse  # noqa: E402
from app.services.gate_market_research_service import GateMarketResearchService  # noqa: E402
from app.services.gate_testnet_market_session_service import GateTestnetMarketSessionRequest, GateTestnetMarketSessionService  # noqa: E402
from app.services.gate_research_run_service import GateResearchRunService  # noqa: E402
from app.services.research_run_result_service import ResearchRunResultService, ResearchRunResultServiceError  # noqa: E402
from app.domain.production_readiness_contracts import ProductionReadinessEvidence, ProductionReadinessError, ProductionReadinessStatus, derive_production_readiness  # noqa: E402
from app.services.production_readiness_service import ProductionReadinessService, ProductionReadinessServiceError  # noqa: E402
from app.services.gate_testnet_rehearsal_service import GateTestnetRehearsalService, GateTestnetRehearsalServiceError  # noqa: E402
from app.services.gate_testnet_rehearsal_result_service import GateTestnetRehearsalResultService, GateTestnetRehearsalResultServiceError  # noqa: E402


UTC = timezone.utc


def _bars():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        BacktestBar(
            "BTC-USDT",
            start + timedelta(minutes=index),
            start + timedelta(minutes=index + 1),
            Decimal(open_).quantize(Decimal("0.01")),
            Decimal(high).quantize(Decimal("0.01")),
            Decimal(low).quantize(Decimal("0.01")),
            Decimal(close).quantize(Decimal("0.01")),
            Decimal("2"),
            index,
            "dataset-1",
        )
        for index, (open_, high, low, close) in enumerate(
            ((100, 101, 99, 100), (100, 102, 99, 101), (101, 103, 100, 102), (102, 106, 98, 99))
        )
    )


def _strategy(family):
    return StrategyDefinition(
        "strategy-1", "v1", family, "schema-1", "data-1",
        (StrategyParameterFact("lookback", "3"),),
    )


class StrategySimulationServiceTests(unittest.TestCase):
    def test_smc_factory_emits_typed_signal(self):
        signal = StrategyFactory().generate_signal(
            _strategy(StrategyFamily.SMC), _bars(), signal_id="signal-1", data_snapshot_id="snapshot-1"
        )
        self.assertEqual(signal.instrument_id, "BTC-USDT")
        self.assertEqual(signal.strategy.family, StrategyFamily.SMC)

    def test_unsupported_strategy_family_fails_closed(self):
        with self.assertRaises(StrategyFactoryError):
            StrategyFactory().generate_signal(
                _strategy(StrategyFamily.BUY_AND_HOLD), _bars(), signal_id="signal-1", data_snapshot_id="snapshot-1"
            )

    def test_paper_shadow_service_preserves_risk_disposition(self):
        signal = StrategyFactory().generate_signal(
            _strategy(StrategyFamily.ICT), _bars(), signal_id="signal-1", data_snapshot_id="snapshot-1"
        )
        request = PositionSizingRequest("request-1", "BTC-USDT", Decimal("100"), Decimal("1"), Decimal("1000"), Decimal("20000"), Decimal("2"), Decimal("0.5"), datetime.now(UTC))
        sizing = PortfolioRiskService().evaluate(request)
        run = PaperShadowRunFacts("run-1", SimulationMode.PAPER, "dataset-1", "strategy-1", "risk-1", "tolerance-1", datetime.now(UTC))
        decision = PaperShadowService().decide(run, signal, sizing, request_fingerprint="request-1", decided_at=datetime.now(UTC))
        self.assertEqual(decision.run_id, "run-1")
        self.assertEqual(decision.mode, SimulationMode.PAPER)

    def test_research_pipeline_connects_strategy_risk_and_simulation(self):
        request = PositionSizingRequest("request-2", "BTC-USDT", Decimal("100"), Decimal("1"), Decimal("1000"), Decimal("20000"), Decimal("2"), Decimal("0.5"), datetime.now(UTC))
        run = PaperShadowRunFacts("run-2", SimulationMode.SHADOW, "dataset-1", "strategy-1", "risk-1", "tolerance-1", datetime.now(UTC))
        result = ResearchPipeline().evaluate(
            _strategy(StrategyFamily.SMC), _bars(), request, run,
            signal_id="signal-2", data_snapshot_id="snapshot-2",
            request_fingerprint="request-2", decided_at=datetime.now(UTC),
        )
        self.assertEqual(result.simulation.mode, SimulationMode.SHADOW)
        self.assertEqual(result.signal.strategy.family, StrategyFamily.SMC)
        self.assertEqual(len(result.result_fingerprint), 64)
        public = result.to_public_dict()
        self.assertEqual(public["sizing"]["request_fingerprint"], "request-2")
        self.assertNotIn("e+", repr(public))

    def test_paper_shadow_batch_replays_and_conflicts_deterministically(self):
        signal = StrategyFactory().generate_signal(
            _strategy(StrategyFamily.SMC), _bars(), signal_id="signal-batch", data_snapshot_id="snapshot-1"
        )
        request = PositionSizingRequest("request-batch", "BTC-USDT", Decimal("100"), Decimal("1"), Decimal("1000"), Decimal("20000"), Decimal("2"), Decimal("0.5"), datetime.now(UTC))
        sizing = PortfolioRiskService().evaluate(request)
        run = PaperShadowRunFacts("run-batch", SimulationMode.PAPER, "dataset-1", "strategy-1", "risk-1", "tolerance-1", datetime.now(UTC))
        candidate = PaperShadowCandidate(signal, sizing, "request-batch", datetime.now(UTC))
        service = PaperShadowService()
        created = service.record_batch(run, (candidate,))
        self.assertEqual(created.disposition, SimulationRecordDisposition.CREATED)
        replayed = service.record_batch(run, (candidate,), existing=created.decision_set)
        self.assertEqual(replayed.disposition, SimulationRecordDisposition.REPLAYED)
        conflicting = PaperShadowCandidate(signal, sizing, "request-batch", datetime.now(UTC) + timedelta(seconds=1))
        with self.assertRaises(ValueError):
            service.record_batch(run, (conflicting,), existing=created.decision_set)

    def test_gate_dataset_enters_the_same_strategy_risk_paper_pipeline(self):
        raw = [
            [1767225600, "200", "101", "102", "99", "100", "2", True],
            [1767225660, "202", "102", "103", "99", "101", "2", True],
            [1767225720, "204", "103", "104", "100", "102", "2", True],
            [1767225780, "206", "104", "105", "101", "103", "2", True],
        ]
        candles = normalize_gate_candles(
            raw, market_type=AssetMarketType.SPOT, instrument_id="BTC_USDT", interval="1m",
            observed_at=datetime(2026, 1, 1, 0, 4, tzinfo=UTC), source_event_prefix="fixture",
            snapshot_id="dataset-gate-1", rule_version="rules-1", evidence_hash_prefix="evidence",
        )
        dataset = build_gate_backtest_dataset(
            candles, dataset_snapshot_id="dataset-gate-1", as_of=datetime(2026, 1, 1, 0, 4, tzinfo=UTC),
        )
        request = PositionSizingRequest("request-gate", "BTC_USDT", Decimal("100"), Decimal("1"), Decimal("1000"), Decimal("20000"), Decimal("2"), Decimal("0.5"), datetime.now(UTC))
        run = PaperShadowRunFacts("run-gate", SimulationMode.SHADOW, "dataset-gate-1", "strategy-1", "risk-1", "tolerance-1", datetime.now(UTC))
        result = ResearchPipeline().evaluate_dataset(
            _strategy(StrategyFamily.ICT), dataset, request, run,
            signal_id="signal-gate", request_fingerprint="request-gate", decided_at=datetime.now(UTC),
        )
        self.assertEqual(result.signal.data_snapshot_id, "dataset-gate-1")
        self.assertEqual(result.simulation.mode, SimulationMode.SHADOW)

    def test_research_pipeline_applies_cooldown_before_paper_shadow(self):
        request = PositionSizingRequest("request-cooldown", "BTC-USDT", Decimal("100"), Decimal("1"), Decimal("1000"), Decimal("20000"), Decimal("2"), Decimal("0.5"), datetime.now(UTC))
        run = PaperShadowRunFacts("run-cooldown", SimulationMode.PAPER, "dataset-1", "strategy-1", "risk-1", "tolerance-1", datetime.now(UTC))
        now = datetime(2026, 1, 1, tzinfo=UTC)
        cooldown = CooldownFact("account-1", "BTC-USDT", CooldownState.ACTIVE, now + timedelta(hours=1), "loss-limit")
        result = ResearchPipeline().evaluate(
            _strategy(StrategyFamily.SMC), _bars(), request, run,
            signal_id="signal-cooldown", data_snapshot_id="snapshot-2",
            request_fingerprint="request-cooldown", decided_at=now,
            cooldown=cooldown, now=now,
        )
        self.assertEqual(result.sizing.disposition.value, "denied")
        self.assertEqual(result.sizing.reason, "cooldown_active")
        self.assertEqual(result.simulation.disposition.value, "REJECTED")

    def test_gate_read_session_enters_the_same_pipeline_without_writes(self):
        raw = [
            [1767225600, "200", "101", "102", "99", "100", "2", True],
            [1767225660, "202", "102", "103", "99", "101", "2", True],
            [1767225720, "204", "103", "104", "100", "102", "2", True],
            [1767225780, "206", "104", "105", "101", "103", "2", True],
        ]
        def transport(request):
            if request.endpoint is GatePublicReadEndpoint.CANDLESTICKS:
                return GateReadResponse(200, raw)
            return GateReadResponse(200, {"id": 7, "current": 1767225720000, "update": 1767225719000, "bids": [["100", "1"]], "asks": [["101", "2"]]})
        profile = GateReadCapabilityProfile(GateEnvironment.TESTNET, GateMarketType.SPOT, credential_ref="fixture")
        session = GateTestnetMarketSessionService(GateMarketResearchService(GateReadonlyAdapter(profile, transport), "fixture", "evidence"))
        request = GateTestnetMarketSessionRequest("BTC_USDT", datetime(2026, 1, 1, 0, 4, tzinfo=UTC), "dataset-run", "rules-1")
        sizing = PositionSizingRequest("request-run", "BTC_USDT", Decimal("100"), Decimal("1"), Decimal("1000"), Decimal("20000"), Decimal("2"), Decimal("0.5"), datetime.now(UTC))
        run = PaperShadowRunFacts("run-gate", SimulationMode.SHADOW, "dataset-run", "strategy-1", "risk-1", "tolerance-1", datetime.now(UTC))
        result = GateResearchRunService(session).execute(request, _strategy(StrategyFamily.ICT), sizing, run, signal_id="signal-run", request_fingerprint="request-run", decided_at=datetime(2026, 1, 1, 0, 4, tzinfo=UTC))
        self.assertEqual(result.dataset.venue, "gate")
        self.assertEqual(result.pipeline.simulation.mode, SimulationMode.SHADOW)
        self.assertFalse(result.to_public_dict()["live_enabled"])

    def test_research_run_result_surface_is_read_only_and_fail_closed(self):
        status, body = ResearchRunResultService().read_response()
        self.assertEqual(status, 503)
        self.assertFalse(body["live_enabled"])
        with self.assertRaises(ResearchRunResultServiceError):
            ResearchRunResultService(lambda: {"run": "not-typed"}).read_response()

    def test_production_readiness_is_evidence_only_and_never_enables_live(self):
        evidence = ProductionReadinessEvidence(True, True, True, True, True, True, True, False)
        self.assertEqual(derive_production_readiness(evidence), ProductionReadinessStatus.CANARY_READY)
        approved = ProductionReadinessEvidence(True, True, True, True, True, True, True, True)
        self.assertEqual(derive_production_readiness(approved), ProductionReadinessStatus.PRODUCTION_READY)
        with self.assertRaises(ProductionReadinessError):
            ProductionReadinessEvidence(True, True, True, True, True, True, True, True, True)

    def test_release_readiness_surface_is_read_only(self):
        evidence = ProductionReadinessEvidence(True, True, True, True, True, True, True, True)
        status, body = ProductionReadinessService(lambda: evidence).read_response()
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "PRODUCTION_READY")
        self.assertFalse(body["live_enabled"])
        with self.assertRaises(ProductionReadinessServiceError):
            ProductionReadinessService(lambda: {"live": True}).read_response()

    def test_gate_testnet_rehearsal_is_ordered_and_repeatable(self):
        raw = [
            [1767225600, "200", "101", "102", "99", "100", "2", True],
            [1767225660, "202", "102", "103", "99", "101", "2", True],
            [1767225720, "204", "103", "104", "100", "102", "2", True],
            [1767225780, "206", "104", "105", "101", "103", "2", True],
        ]
        def transport(request):
            if request.endpoint is GatePublicReadEndpoint.CANDLESTICKS:
                return GateReadResponse(200, raw)
            return GateReadResponse(200, {"id": 7, "current": 1767225720000, "update": 1767225719000, "bids": [["100", "1"]], "asks": [["101", "2"]]})
        profile = GateReadCapabilityProfile(GateEnvironment.TESTNET, GateMarketType.SPOT, credential_ref="fixture")
        session = GateTestnetMarketSessionService(GateMarketResearchService(GateReadonlyAdapter(profile, transport), "fixture", "evidence"))
        first = GateTestnetRehearsalService(session).run((
            GateTestnetMarketSessionRequest("BTC_USDT", datetime(2026, 1, 1, 0, 4, tzinfo=UTC), "rehearsal-1", "rules-1"),
            GateTestnetMarketSessionRequest("BTC_USDT", datetime(2026, 1, 1, 0, 5, tzinfo=UTC), "rehearsal-2", "rules-1"),
        ))
        self.assertEqual(first.status.value, "READY")
        self.assertEqual(first.to_public_dict()["snapshot_count"], 2)
        with self.assertRaises(GateTestnetRehearsalServiceError):
            GateTestnetRehearsalService(session).run((
                GateTestnetMarketSessionRequest("BTC_USDT", datetime(2026, 1, 1, 0, 5, tzinfo=UTC), "rehearsal-2", "rules-1"),
                GateTestnetMarketSessionRequest("BTC_USDT", datetime(2026, 1, 1, 0, 5, tzinfo=UTC), "rehearsal-3", "rules-1"),
            ))

    def test_rehearsal_result_surface_is_read_only(self):
        status, body = GateTestnetRehearsalResultService().read_response()
        self.assertEqual(status, 503)
        self.assertFalse(body["live_enabled"])
        with self.assertRaises(GateTestnetRehearsalResultServiceError):
            GateTestnetRehearsalResultService(lambda: {"live": True}).read_response()


if __name__ == "__main__":
    unittest.main()
