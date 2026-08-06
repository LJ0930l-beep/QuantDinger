import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    names = ["app", "app.domain", "app.domain.deterministic_backtest_contracts", "app.domain.deterministic_backtest_runner_contracts"]
    old = {name: sys.modules.get(name) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]; sys.modules["app"] = app; sys.modules["app.domain"] = domain
        for name, path in ((names[2], ROOT / "app/domain/deterministic_backtest_contracts.py"), (names[3], ROOT / "app/domain/deterministic_backtest_runner_contracts.py")):
            spec = importlib.util.spec_from_file_location(name, path); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
        return sys.modules[names[3]], sys.modules[names[2]]
    finally:
        for name in reversed(names):
            if old[name] is None: sys.modules.pop(name, None)
            else: sys.modules[name] = old[name]


M, BT = load()


def bar(sequence, start):
    return BT.BacktestBar("BTC_USDT", start, start + timedelta(minutes=1), Decimal("100"), Decimal("102"), Decimal("99"), Decimal("101"), Decimal("10"), sequence, "dataset-1")


def run():
    return BT.BacktestRunFacts("run-1", "dataset-1", "rules", "fees", "slip", Decimal("1000"), "USDT", UTC, UTC + timedelta(hours=1))


class DeterministicBacktestRunnerTests(unittest.TestCase):
    def test_market_and_limit_orders_replay_at_later_bar(self):
        bars = (bar(1, UTC + timedelta(minutes=1)), bar(2, UTC + timedelta(minutes=3)))
        orders = (
            BT.BacktestOrderIntent("market", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.MARKET, Decimal("1"), UTC),
            BT.BacktestOrderIntent("limit", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.LIMIT, Decimal("1"), UTC + timedelta(minutes=2), Decimal("99")),
        )
        trace = M.run_deterministic_backtest(run(), bars, orders)
        self.assertEqual([item.order_id for item in trace.decisions], ["market", "limit"])
        self.assertEqual(trace.decisions[0].decision, BT.BacktestDecision.EXECUTED)
        self.assertEqual(trace.decisions[1].decision, BT.BacktestDecision.EXECUTED)

    def test_stop_orders_use_explicit_trigger_facts(self):
        bars = (bar(1, UTC + timedelta(minutes=1)), bar(2, UTC + timedelta(minutes=3)))
        order = BT.BacktestOrderIntent(
            "stop", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.STOP_MARKET,
            Decimal("1"), UTC + timedelta(minutes=2), trigger_price=Decimal("101"),
            trigger_direction=BT.BacktestTriggerDirection.CROSS_UP,
            trigger_price_type=BT.BacktestTriggerPriceType.HIGH_LOW,
        )
        trace = M.run_deterministic_backtest(run(), bars, (order,))
        self.assertEqual(trace.decisions[0].decision, BT.BacktestDecision.EXECUTED)
        self.assertEqual(trace.decisions[0].fill_price, Decimal("101"))

    def test_same_bar_and_no_later_bar_are_invalid(self):
        order = BT.BacktestOrderIntent("o", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.MARKET, Decimal("1"), UTC + timedelta(minutes=1))
        trace = M.run_deterministic_backtest(run(), (bar(1, UTC + timedelta(minutes=1)),), (order,))
        self.assertEqual(trace.decisions[0].decision, BT.BacktestDecision.INVALID)

    def test_snapshot_order_and_clock_scope_fail_closed(self):
        order = BT.BacktestOrderIntent("o", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.MARKET, Decimal("1"), UTC)
        with self.assertRaises(M.DeterministicBacktestRunnerError):
            M.run_deterministic_backtest(run(), (bar(1, UTC + timedelta(minutes=1)),), (order, order))
        with self.assertRaises(M.DeterministicBacktestRunnerError):
            M.run_deterministic_backtest(run(), (BT.BacktestBar("BTC_USDT", UTC + timedelta(minutes=1), UTC + timedelta(minutes=2), Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("1"), 1, "other"),), (order,))

    def test_trace_fingerprint_is_deterministic(self):
        bars = (bar(1, UTC + timedelta(minutes=1)),)
        order = BT.BacktestOrderIntent("o", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.MARKET, Decimal("1"), UTC)
        first = M.run_deterministic_backtest(run(), bars, (order,))
        second = M.run_deterministic_backtest(run(), bars, (order,))
        self.assertEqual(first.trace_fingerprint, second.trace_fingerprint)
        with self.assertRaises((AttributeError, TypeError)):
            first.trace_fingerprint = "x"

    def test_trace_identity_includes_cost_policy_versions(self):
        bars = (bar(1, UTC + timedelta(minutes=1)),)
        order = BT.BacktestOrderIntent("o", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.MARKET, Decimal("1"), UTC)
        first = M.run_deterministic_backtest(run(), bars, (order,))
        changed = M.run_deterministic_backtest(
            BT.BacktestRunFacts("run-1", "dataset-1", "rules", "fees-v2", "slip", Decimal("1000"), "USDT", UTC, UTC + timedelta(hours=1)),
            bars,
            (order,),
        )
        assert first.fee_policy_version == "fees"
        assert changed.fee_policy_version == "fees-v2"
        assert first.trace_fingerprint != changed.trace_fingerprint

    def test_trace_identity_includes_cost_policy_fingerprint(self):
        bars = (bar(1, UTC + timedelta(minutes=1)),)
        order = BT.BacktestOrderIntent("o", "BTC_USDT", BT.BacktestSide.BUY, BT.BacktestExecutionKind.MARKET, Decimal("1"), UTC)
        first = M.run_deterministic_backtest(
            BT.BacktestRunFacts("run-1", "dataset-1", "rules", "fees", "slip", Decimal("1000"), "USDT", UTC, UTC + timedelta(hours=1), "a" * 64), bars, (order,)
        )
        second = M.run_deterministic_backtest(
            BT.BacktestRunFacts("run-1", "dataset-1", "rules", "fees", "slip", Decimal("1000"), "USDT", UTC, UTC + timedelta(hours=1), "b" * 64), bars, (order,)
        )
        assert first.cost_policy_fingerprint == "a" * 64
        assert first.trace_fingerprint != second.trace_fingerprint


if __name__ == "__main__": unittest.main()
