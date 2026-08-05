"""SC-13 source/mode convergence gate tests without app startup."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


_MISSING = object()


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _contracts() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = ("app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts", "app.domain.canonical_entry_contracts", "app.domain.canonical_entry_v2_contracts", "app.domain.entry_convergence_gate_contracts")
    previous = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app"); app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain"); domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        loaded = {}
        for name in names[2:]:
            short = name.rsplit(".", 1)[-1]
            loaded[short] = _load(name, app_dir / "domain" / f"{short}.py")
        return SimpleNamespace(**loaded)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


m = _contracts()


def _request(source, actor, action=None):
    order = m.order_contracts; entry = m.canonical_entry_contracts; v2 = m.canonical_entry_v2_contracts
    action = action or (order.OrderAction.PROTECTION if source is entry.EntrySource.PROTECTION else order.OrderAction.OPEN)
    effect = order.RiskEffect.NEUTRAL if action is order.OrderAction.CANCEL else (order.RiskEffect.INCREASE_RISK if action in (order.OrderAction.OPEN, order.OrderAction.INCREASE) else order.RiskEffect.REDUCE_RISK)
    values = {"side": entry.OrderSide.BUY, "quantity": m.decimal_values.Quantity("1"), "quantity_semantics": v2.QuantitySemantics.ABSOLUTE, "execution_kind": entry.ExecutionKind.MARKET}
    if action is order.OrderAction.PROTECTION:
        values.update({"quantity": None, "quantity_semantics": None, "reduce_only": True, "target_position_id": "position-1", "close_all": True})
    return v2.CanonicalEntryRequestV2(
        tenant_id=1, credential_id=2, account_scope="paper-main", instrument_id="BTC-USDT", market_type="swap", action=action,
        economic_intent=v2.CanonicalEconomicIntentV2(**values), actor=entry.EntryActorContext(actor, "actor-1", source), risk_effect=effect,
        idempotency_key="case-1", correlation_id="corr-1", occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


class EntryConvergenceGateTests(unittest.TestCase):
    def test_every_source_is_typed_and_live_is_absent(self):
        values = (("REST", "HUMAN"), ("MANUAL", "HUMAN"), ("STRATEGY", "STRATEGY"), ("PROTECTION", "PROTECTION"), ("AGENT", "AGENT"), ("MCP", "MCP"), ("GRID", "GRID"))
        for source_name, actor_name in values:
            source = m.canonical_entry_contracts.EntrySource[source_name]; actor = m.order_contracts.Actor[actor_name]
            decision = m.entry_convergence_gate_contracts.validate_entry_surface(source, m.canonical_entry_contracts.EntryMode.PAPER, _request(source, actor))
            self.assertEqual(decision.request.actor.entry_source, source)
            self.assertNotEqual(decision.mode.value, "LIVE")

    def test_restricted_sources_default_disabled_but_paper_is_canonical_only(self):
        for source in (m.canonical_entry_contracts.EntrySource.AGENT, m.canonical_entry_contracts.EntrySource.MCP, m.canonical_entry_contracts.EntrySource.GRID):
            policy = m.entry_convergence_gate_contracts.default_entry_surface_policy(source)
            self.assertIs(policy.default_mode, m.canonical_entry_contracts.EntryMode.DISABLED)
            decision = m.entry_convergence_gate_contracts.validate_entry_surface(source, m.canonical_entry_contracts.EntryMode.PAPER, _request(source, m.order_contracts.Actor[source.value]))
            self.assertIs(decision.disposition, m.entry_convergence_gate_contracts.EntrySurfaceDisposition.CANONICAL_ONLY)
            self.assertFalse(decision.admission_required)

    def test_disabled_mode_has_no_admission_side_effect_contract(self):
        source = m.canonical_entry_contracts.EntrySource.REST
        decision = m.entry_convergence_gate_contracts.validate_entry_surface(source, m.canonical_entry_contracts.EntryMode.DISABLED, _request(source, m.order_contracts.Actor.HUMAN))
        self.assertIs(decision.disposition, m.entry_convergence_gate_contracts.EntrySurfaceDisposition.DISABLED)
        self.assertFalse(decision.admission_required)

    def test_source_mismatch_and_live_injection_fail_closed(self):
        source = m.canonical_entry_contracts.EntrySource.REST
        with self.assertRaises(m.entry_convergence_gate_contracts.EntryConvergenceError):
            m.entry_convergence_gate_contracts.validate_entry_surface(source, m.canonical_entry_contracts.EntryMode.PAPER, _request(m.canonical_entry_contracts.EntrySource.STRATEGY, m.order_contracts.Actor.STRATEGY))
        with self.assertRaises(ValueError):
            m.entry_convergence_gate_contracts.EntrySurfacePolicy(source, m.canonical_entry_contracts.EntryMode.PAPER, (m.canonical_entry_contracts.EntryMode.PAPER,), "LIVE")


if __name__ == "__main__":
    unittest.main()
