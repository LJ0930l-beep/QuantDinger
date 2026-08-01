import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    name = "app.domain.portfolio_exposure_contracts"; names = ["app", "app.domain", name]; old = {n: sys.modules.get(n) for n in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]; sys.modules["app"] = app; sys.modules["app.domain"] = domain
        spec = importlib.util.spec_from_file_location(name, ROOT / "app/domain/portfolio_exposure_contracts.py"); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
    finally:
        for n in reversed(names):
            if old[n] is None: sys.modules.pop(n, None)
            else: sys.modules[n] = old[n]


M = load()


def position(**changes):
    facts = dict(position_id="p-1", account_scope="paper", instrument_id="BTC_USDT", side=M.ExposureSide.LONG, quantity=Decimal("1"), mark_price=Decimal("100"), observed_at=UTC)
    facts.update(changes); return M.PositionExposureFact(**facts)


class PortfolioExposureTests(unittest.TestCase):
    def test_gross_and_net_are_separate_signed_facts(self):
        snapshot = M.PortfolioExposureSnapshot("paper", UTC, (position(), position(position_id="p-2", side=M.ExposureSide.SHORT, quantity=Decimal("0.5"))))
        self.assertEqual(snapshot.gross_exposure, Decimal("150")); self.assertEqual(snapshot.net_exposure, Decimal("50"))

    def test_limits_deny_gross_or_net_breach(self):
        snapshot = M.PortfolioExposureSnapshot("paper", UTC, (position(),))
        gross = M.evaluate_portfolio_exposure_limit(snapshot, side=M.ExposureSide.LONG, additional_quantity=Decimal("1"), mark_price=Decimal("100"), max_gross_exposure=Decimal("150"), max_abs_net_exposure=Decimal("1000"))
        self.assertEqual(gross.disposition, M.ExposureLimitDisposition.DENIED)
        net = M.evaluate_portfolio_exposure_limit(snapshot, side=M.ExposureSide.LONG, additional_quantity=Decimal("1"), mark_price=Decimal("100"), max_gross_exposure=Decimal("1000"), max_abs_net_exposure=Decimal("150"))
        self.assertEqual(net.reason, "net_exposure_exceeded")

    def test_scope_duplicate_and_future_facts_fail_closed(self):
        with self.assertRaises(M.PortfolioExposureError): M.PortfolioExposureSnapshot("paper", UTC, (position(), position(position_id="p-1")))
        with self.assertRaises(M.PortfolioExposureError): M.PortfolioExposureSnapshot("paper", UTC, (position(account_scope="other"),))
        with self.assertRaises(M.PortfolioExposureError): M.PortfolioExposureSnapshot("paper", UTC, (position(observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),))

    def test_decimal_and_immutability_contracts(self):
        with self.assertRaises(M.PortfolioExposureError): position(quantity=1.0)
        snapshot = M.PortfolioExposureSnapshot("paper", UTC, (position(),))
        with self.assertRaises((AttributeError, TypeError)): snapshot.gross_exposure = Decimal("0")


if __name__ == "__main__": unittest.main()
