"""Pure tests for the account cooldown contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
UTC = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module


def _contracts() -> SimpleNamespace:
    names = ("app", "app.domain", "app.domain.order_contracts", "app.domain.cooldown_policy_contracts")
    missing = object(); previous = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(ROOT / "app")]
        domain = ModuleType("app.domain"); domain.__path__ = [str(ROOT / "app" / "domain")]
        sys.modules["app"] = app; sys.modules["app.domain"] = domain
        order = _load(names[2], ROOT / "app" / "domain" / "order_contracts.py")
        cooldown = _load(names[3], ROOT / "app" / "domain" / "cooldown_policy_contracts.py")
        return SimpleNamespace(order=order, cooldown=cooldown)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is missing: sys.modules.pop(name, None)
            else: sys.modules[name] = original


C = _contracts()


class CooldownPolicyContractTests(unittest.TestCase):
    def test_increase_is_blocked_until_time_and_three_cycles(self):
        before_time = C.cooldown.evaluate_cooldown(action=C.order.OrderAction.OPEN, started_at=UTC, now_utc=UTC + timedelta(hours=11), completed_trade_cycles=3)
        before_cycles = C.cooldown.evaluate_cooldown(action=C.order.OrderAction.OPEN, started_at=UTC, now_utc=UTC + timedelta(hours=12), completed_trade_cycles=2)
        released = C.cooldown.evaluate_cooldown(action=C.order.OrderAction.OPEN, started_at=UTC, now_utc=UTC + timedelta(hours=12), completed_trade_cycles=3)
        self.assertEqual(before_time.disposition, C.cooldown.CooldownDisposition.ACTIVE)
        self.assertEqual(before_cycles.disposition, C.cooldown.CooldownDisposition.ACTIVE)
        self.assertEqual(released.disposition, C.cooldown.CooldownDisposition.RELEASED)

    def test_reducing_and_neutral_actions_are_not_blocked(self):
        for action in (C.order.OrderAction.REDUCE, C.order.OrderAction.CLOSE, C.order.OrderAction.CANCEL):
            result = C.cooldown.evaluate_cooldown(action=action, started_at=UTC, now_utc=UTC + timedelta(hours=1), completed_trade_cycles=0)
            self.assertEqual(result.disposition, C.cooldown.CooldownDisposition.RELEASED)

    def test_policy_and_utc_inputs_fail_closed(self):
        with self.assertRaises(C.cooldown.CooldownPolicyError):
            C.cooldown.CooldownPolicy(minimum_hours=1)
        with self.assertRaises(C.cooldown.CooldownPolicyError):
            C.cooldown.evaluate_cooldown(action=C.order.OrderAction.OPEN, started_at=datetime(2026, 8, 1, 12), now_utc=UTC, completed_trade_cycles=0)


if __name__ == "__main__": unittest.main()
