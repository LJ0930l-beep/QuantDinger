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
    name = "app.domain.deterministic_backtest_contracts"
    names = ["app", "app.domain", name]
    old = {item: sys.modules.get(item) for item in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domain" / "deterministic_backtest_contracts.py")
        module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
    finally:
        for item in reversed(names):
            if old[item] is None: sys.modules.pop(item, None)
            else: sys.modules[item] = old[item]


M = load()


def bar(**changes):
    facts = dict(instrument_id="BTC_USDT", open_time=UTC + timedelta(minutes=1), close_time=UTC + timedelta(minutes=2), open_price=Decimal("100"), high_price=Decimal("102"), low_price=Decimal("99"), close_price=Decimal("101"), volume=Decimal("10"), sequence=1, snapshot_id="dataset-1")
    facts.update(changes); return M.BacktestBar(**facts)


class DeterministicBacktestTests(unittest.TestCase):
    def test_run_facts_are_decimal_and_utc(self):
        run = M.BacktestRunFacts("run-1", "dataset-1", "rules-1", "fees-1", "slip-1", Decimal("1000"), "USDT", UTC, UTC + timedelta(days=1))
        self.assertEqual(run.initial_cash, Decimal("1000"))
        with self.assertRaises(M.BacktestContractError): M.BacktestRunFacts("run", "d", "r", "f", "s", 1.0, "USDT", UTC, UTC + timedelta(days=1))

    def test_next_open_market_and_same_bar_guard(self):
        order = M.BacktestOrderIntent("o-1", "BTC_USDT", M.BacktestSide.BUY, M.BacktestExecutionKind.MARKET, Decimal("1"), UTC)
        decision = M.next_open_execution(order, bar())
        self.assertEqual(decision.decision, M.BacktestDecision.EXECUTED); self.assertEqual(decision.fill_price, Decimal("100"))
        self.assertEqual(M.next_open_execution(order, bar(open_time=UTC)).decision, M.BacktestDecision.INVALID)

    def test_limit_next_open_or_not_reached(self):
        order = M.BacktestOrderIntent("o-1", "BTC_USDT", M.BacktestSide.BUY, M.BacktestExecutionKind.LIMIT, Decimal("1"), UTC, Decimal("99"))
        self.assertEqual(M.next_open_execution(order, bar()).decision, M.BacktestDecision.EXECUTED)
        self.assertEqual(M.next_open_execution(order, bar(low_price=Decimal("100"))).decision, M.BacktestDecision.NOT_EXECUTED)
        with self.assertRaises(M.BacktestContractError): M.BacktestOrderIntent("o", "BTC_USDT", M.BacktestSide.BUY, M.BacktestExecutionKind.LIMIT, Decimal("1"), UTC)

    def test_sell_limit_and_bad_bounds_fail_closed(self):
        order = M.BacktestOrderIntent("o-1", "BTC_USDT", M.BacktestSide.SELL, M.BacktestExecutionKind.LIMIT, Decimal("1"), UTC, Decimal("102"))
        self.assertEqual(M.next_open_execution(order, bar()).decision, M.BacktestDecision.EXECUTED)
        with self.assertRaises(M.BacktestContractError): bar(high_price=Decimal("98"))

    def test_fingerprint_is_stable_and_scope_sensitive(self):
        order = M.BacktestOrderIntent("o-1", "BTC_USDT", M.BacktestSide.BUY, M.BacktestExecutionKind.MARKET, Decimal("1.00"), UTC)
        same = M.BacktestOrderIntent("o-1", "BTC_USDT", M.BacktestSide.BUY, M.BacktestExecutionKind.MARKET, Decimal("1"), UTC)
        self.assertEqual(M.backtest_fingerprint(order), M.backtest_fingerprint(same))
        self.assertNotEqual(M.backtest_fingerprint(order), M.backtest_fingerprint(M.BacktestOrderIntent("o-2", "BTC_USDT", M.BacktestSide.BUY, M.BacktestExecutionKind.MARKET, Decimal("1"), UTC)))

    def test_decision_requires_consistent_fill_facts(self):
        with self.assertRaises(M.BacktestContractError): M.BacktestExecutionDecision("o", M.BacktestDecision.EXECUTED, None, None, "bad")
        with self.assertRaises(M.BacktestContractError): M.BacktestExecutionDecision("o", M.BacktestDecision.NOT_EXECUTED, UTC, Decimal("1"), "bad")


if __name__ == "__main__": unittest.main()
