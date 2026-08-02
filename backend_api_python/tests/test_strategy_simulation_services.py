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


if __name__ == "__main__":
    unittest.main()
