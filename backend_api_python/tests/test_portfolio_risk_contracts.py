import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]; UTC = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load():
    name = "app.domain.portfolio_risk_contracts"; names = ["app", "app.domain", name]; old = {n: sys.modules.get(n) for n in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]; domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]; sys.modules["app"] = app; sys.modules["app.domain"] = domain
        spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domain" / "portfolio_risk_contracts.py"); module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module
    finally:
        for n in reversed(names):
            if old[n] is None: sys.modules.pop(n, None)
            else: sys.modules[n] = old[n]


M = load()


def request(**changes):
    facts = dict(request_fingerprint="req-1", instrument_id="BTC_USDT", mark_price=Decimal("100"), requested_quantity=Decimal("1"), available_margin=Decimal("1000"), max_notional=Decimal("1000"), max_leverage=Decimal("5"), margin_rate=Decimal("0.2"), observed_at=UTC)
    facts.update(changes); return M.PositionSizingRequest(**facts)


class PortfolioRiskTests(unittest.TestCase):
    def test_allow_within_limits(self):
        decision = M.evaluate_position_sizing(request()); self.assertEqual(decision.disposition, M.SizingDisposition.ALLOWED); self.assertEqual(decision.notional, Decimal("100")); self.assertEqual(decision.required_margin, Decimal("20"))

    def test_deny_exposure_and_margin_neutral(self):
        for facts, reason in ((dict(max_notional=Decimal("10")), "max_notional_exceeded"), (dict(available_margin=Decimal("1")), "available_margin_exceeded"), (dict(max_leverage=Decimal("2")), "max_leverage_exceeded")):
            decision = M.evaluate_position_sizing(request(**facts)); self.assertEqual(decision.disposition, M.SizingDisposition.DENIED); self.assertEqual(decision.reason, reason); self.assertEqual(decision.approved_quantity, Decimal("0"))

    def test_zero_margin_rate_fails_closed_as_typed_deny(self):
        decision = M.evaluate_position_sizing(request(margin_rate=Decimal("0")))
        self.assertEqual(decision.disposition, M.SizingDisposition.DENIED)
        self.assertEqual(decision.reason, "max_leverage_exceeded")
        self.assertEqual(decision.required_margin, Decimal("0"))

    def test_decimal_float_and_time_are_strict(self):
        with self.assertRaises(M.PortfolioRiskError): request(mark_price=1.0)
        with self.assertRaises(M.PortfolioRiskError): request(observed_at=datetime(2026, 1, 1))

    def test_cooldown_requires_expiry_when_active(self):
        inactive = M.CooldownFact("paper", "BTC_USDT", M.CooldownState.INACTIVE, None, "none"); self.assertEqual(inactive.state, M.CooldownState.INACTIVE)
        with self.assertRaises(M.PortfolioRiskError): M.CooldownFact("paper", "BTC_USDT", M.CooldownState.ACTIVE, None, "loss")

    def test_fingerprint_is_stable(self):
        self.assertEqual(M.portfolio_risk_fingerprint(request(mark_price=Decimal("100.00"))), M.portfolio_risk_fingerprint(request(mark_price=Decimal("100"))))


if __name__ == "__main__": unittest.main()
