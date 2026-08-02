"""Integration tests for Strategy Factory -> deterministic backtest trace."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def _bootstrap():
    app = types.ModuleType("app")
    app.__path__ = [str(ROOT / "app")]
    domain = types.ModuleType("app.domain")
    domain.__path__ = [str(ROOT / "app" / "domain")]
    services = types.ModuleType("app.services")
    services.__path__ = [str(ROOT / "app" / "services")]
    sys.modules.setdefault("app", app)
    sys.modules.setdefault("app.domain", domain)
    sys.modules.setdefault("app.services", services)


_bootstrap()
for name, relative in (
    ("app.domain.deterministic_backtest_contracts", "domain/deterministic_backtest_contracts.py"),
    ("app.domain.deterministic_backtest_runner_contracts", "domain/deterministic_backtest_runner_contracts.py"),
    ("app.domain.market_data_quality_contracts", "domain/market_data_quality_contracts.py"),
    ("app.domain.backtest_dataset_contracts", "domain/backtest_dataset_contracts.py"),
    ("app.domain.strategy_library_contracts", "domain/strategy_library_contracts.py"),
    ("app.domain.strategy_signal_contracts", "domain/strategy_signal_contracts.py"),
    ("app.services.strategy_factory", "services/strategy_factory.py"),
    ("app.services.deterministic_backtest_service", "services/deterministic_backtest_service.py"),
):
    path = ROOT / "app" / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

BT = sys.modules["app.domain.deterministic_backtest_contracts"]
Q = sys.modules["app.domain.market_data_quality_contracts"]
DS = sys.modules["app.domain.backtest_dataset_contracts"]
SL = sys.modules["app.domain.strategy_library_contracts"]
SVC = sys.modules["app.services.deterministic_backtest_service"]


def _bars():
    values = (
        (100, 102, 99, 100),
        (100, 102, 99, 100),
        (100, 102, 99, 100),
        (100, 103, 98, 101),
        (101, 102, 99, 100),
        (100, 105, 80, 95),  # liquidity sweep below prior lows, close back above
    )
    result = []
    for sequence, (opened, high, low, closed) in enumerate(values):
        start = START + timedelta(minutes=sequence)
        result.append(BT.BacktestBar(
            "BTC_USDT", start, start + timedelta(minutes=1),
            Decimal(opened), Decimal(high), Decimal(low), Decimal(closed),
            Decimal("10"), sequence, "dataset-1",
        ))
    return tuple(result)


def _dataset():
    events = tuple(Q.MarketDataEventFact(
        f"event-{i}", "gate", "BTC_USDT", START + timedelta(minutes=i),
        START + timedelta(minutes=i + 1), i, "dataset-1", "rules-v1", f"payload-{i}"
    ) for i in range(6))
    quality = Q.DataQualityAssessment(Q.DataQualityStatus.COMPLETE, events, (), START + timedelta(hours=1), "quality")
    return DS.BacktestDatasetSnapshot("dataset-1", "gate", "spot", "BTC_USDT", "rules-v1", _bars(), quality, START + timedelta(hours=1))


def _run():
    return BT.BacktestRunFacts(
        "run-1", "dataset-1", "rules-v1", "fees-v1", "slippage-v1",
        Decimal("1000"), "USDT", START, START + timedelta(hours=1),
    )


def _strategy():
    return SL.StrategyDefinition(
        "smc-structure", "smc-v1", SL.StrategyFamily.SMC,
        "parameters-v1", "dataset-rules-v1",
        (SL.StrategyParameterFact("lookback", "3"),),
    )


class DeterministicBacktestServiceTests(unittest.TestCase):
    def test_strategy_factory_trace_uses_later_bar_and_is_replayable(self):
        service = SVC.DeterministicBacktestService()
        first = service.run(_run(), _dataset(), _strategy(), order_quantity=Decimal("1"))
        second = service.run(_run(), _dataset(), _strategy(), order_quantity=Decimal("1"))
        self.assertEqual(first.result_fingerprint, second.result_fingerprint)
        self.assertTrue(first.orders)
        self.assertTrue(any(item.decision is BT.BacktestDecision.EXECUTED for item in first.trace.decisions))
        self.assertTrue(all(
            decision.fill_time is not None and decision.fill_time > order.submitted_at
            for decision, order in zip(first.trace.decisions, first.orders)
            if decision.decision is BT.BacktestDecision.EXECUTED
        ))

    def test_limit_requires_typed_entry_and_float_quantity_fails_closed(self):
        service = SVC.DeterministicBacktestService()
        with self.assertRaises(SVC.DeterministicBacktestServiceError):
            service.run(_run(), _dataset(), _strategy(), order_quantity=1.0)
        result = service.run(
            _run(), _dataset(), _strategy(),
            order_quantity=Decimal("1"),
            execution_kind=BT.BacktestExecutionKind.LIMIT,
        )
        self.assertTrue(result.orders)
        self.assertTrue(all(order.limit_price is not None for order in result.orders))


if __name__ == "__main__":
    unittest.main()
