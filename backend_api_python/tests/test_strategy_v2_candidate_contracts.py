from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


_MISSING = object()


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_candidate_contracts() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app",
        "app.domain",
        "app.domain.decimal_values",
        "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts",
        "app.domain.canonical_entry_v2_contracts",
        "app.domain.entrypoint_v2_binding_contracts",
        "app.domain.strategy_v2_candidate_contracts",
    )
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        decimals = _load("app.domain.decimal_values", app_dir / "domain" / "decimal_values.py")
        order = _load("app.domain.order_contracts", app_dir / "domain" / "order_contracts.py")
        entry = _load("app.domain.canonical_entry_contracts", app_dir / "domain" / "canonical_entry_contracts.py")
        entry_v2 = _load("app.domain.canonical_entry_v2_contracts", app_dir / "domain" / "canonical_entry_v2_contracts.py")
        binding = _load("app.domain.entrypoint_v2_binding_contracts", app_dir / "domain" / "entrypoint_v2_binding_contracts.py")
        candidate = _load("app.domain.strategy_v2_candidate_contracts", app_dir / "domain" / "strategy_v2_candidate_contracts.py")
        return SimpleNamespace(decimals=decimals, order=order, entry=entry, entry_v2=entry_v2, binding=binding, candidate=candidate)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


m = load_candidate_contracts()
UTC = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
COMMAND_ID = "11111111-1111-4111-8111-111111111111"
ECONOMIC_ORDER_ID = "22222222-2222-4222-8222-222222222222"


def _plan(**changes):
    values = {
        "strategy_id": 7,
        "strategy_run_id": 11,
        "signal_id": "signal-001",
        "instrument_id": "BTCUSDT",
        "market_type": "swap",
        "action": m.order.OrderAction.OPEN,
        "side": m.entry.OrderSide.BUY,
        "quantity": "1.25",
        "execution_kind": m.entry.ExecutionKind.MARKET,
    }
    values.update(changes)
    return m.candidate.StrategyV2CandidateTradePlan(**values)


class StrategyV2CandidateContractTests(unittest.TestCase):
    def test_open_candidate_maps_to_canonical_request_and_graph(self):
        plan = _plan()
        request = plan.to_request(
            tenant_id=1, credential_id=2, account_scope="paper-account",
            correlation_id="corr-001", occurred_at=UTC, mode=m.entry.EntryMode.PAPER,
        )
        self.assertEqual(request.action, m.order.OrderAction.OPEN)
        self.assertEqual(request.risk_effect, m.order.RiskEffect.INCREASE_RISK)
        self.assertEqual(request.actor.actor_type, m.order.Actor.STRATEGY)
        self.assertEqual(request.actor.actor_id, "strategy-7")
        self.assertEqual(request.economic_intent.quantity.to_string(), "1.25")
        graph = plan.to_graph(
            command_id=COMMAND_ID, economic_order_id=ECONOMIC_ORDER_ID,
            tenant_id=1, credential_id=2, account_scope="paper-account",
            correlation_id="corr-001", occurred_at=UTC,
        )
        self.assertEqual(graph.command_id, COMMAND_ID)
        self.assertEqual(graph.subject.economic_order_id, ECONOMIC_ORDER_ID)

    def test_idempotency_key_is_stable_and_signal_scoped(self):
        self.assertEqual(_plan().idempotency_key(), _plan().idempotency_key())
        self.assertNotEqual(_plan().idempotency_key(), _plan(signal_id="signal-002").idempotency_key())

    def test_decimal_float_is_rejected_before_admission(self):
        with self.assertRaises(m.candidate.StrategyV2CandidateError):
            _plan(quantity=1.25)

    def test_reducing_partial_and_close_all_keep_distinct_facts(self):
        partial = _plan(
            action=m.order.OrderAction.REDUCE, side=m.entry.OrderSide.SELL,
            quantity=None, reduce_only=True, position_side=m.entry.PositionSide.LONG,
            target_position_id="position-7", close_quantity="0.5",
        )
        close_all = _plan(
            action=m.order.OrderAction.CLOSE, side=m.entry.OrderSide.SELL,
            quantity=None, reduce_only=True, position_side=m.entry.PositionSide.LONG,
            target_position_id="position-7", close_all=True,
        )
        partial_request = partial.to_request(
            tenant_id=1, credential_id=2, account_scope="paper-account",
            correlation_id="corr-1", occurred_at=UTC,
        )
        all_request = close_all.to_request(
            tenant_id=1, credential_id=2, account_scope="paper-account",
            correlation_id="corr-2", occurred_at=UTC,
        )
        self.assertEqual(partial_request.economic_intent.close_quantity.to_string(), "0.5")
        self.assertTrue(all_request.economic_intent.close_all)

    def test_stop_limit_preserves_all_trigger_facts(self):
        plan = _plan(
            execution_kind=m.entry.ExecutionKind.STOP_LIMIT, limit_price="101.0",
            trigger_price="100.0", trigger_direction=m.entry_v2.TriggerDirection.AT_OR_ABOVE,
            trigger_price_type=m.entry_v2.TriggerPriceType.MARK,
        )
        intent = plan.to_request(
            tenant_id=1, credential_id=2, account_scope="paper-account",
            correlation_id="corr-stop", occurred_at=UTC,
        ).economic_intent
        self.assertEqual(intent.limit_price.to_string(), "101")
        self.assertEqual(intent.trigger_price.to_string(), "100")
        self.assertEqual(intent.trigger_direction, m.entry_v2.TriggerDirection.AT_OR_ABOVE)
        self.assertEqual(intent.trigger_price_type, m.entry_v2.TriggerPriceType.MARK)

    def test_invalid_action_facts_fail_closed(self):
        with self.assertRaises(m.candidate.StrategyV2CandidateError):
            _plan(action=m.order.OrderAction.CANCEL)
        with self.assertRaises(m.candidate.StrategyV2CandidateError):
            _plan(quantity=None)
        with self.assertRaises(m.candidate.StrategyV2CandidateError):
            _plan(action=m.order.OrderAction.REDUCE, reduce_only=True, target_position_id="position-7")

    def test_scope_or_identity_errors_are_typed(self):
        with self.assertRaises(m.candidate.StrategyV2CandidateError):
            _plan(strategy_id=0)
        with self.assertRaises(m.candidate.StrategyV2CandidateError):
            _plan(signal_id=" signal")
        with self.assertRaises(m.candidate.StrategyV2CandidateError):
            _plan().to_graph(
                command_id="not-a-uuid", economic_order_id=ECONOMIC_ORDER_ID,
                tenant_id=1, credential_id=2, account_scope="paper-account",
                correlation_id="corr-1", occurred_at=UTC,
            )

    def test_module_is_pure_and_does_not_reference_runtime_or_exchange(self):
        source = Path(__file__).parents[1] / "app/domain/strategy_v2_candidate_contracts.py"
        text = source.read_text(encoding="utf-8").lower()
        for forbidden in ("import flask", "from flask", "psycopg", "commit(", "rollback(", "StrategyV2LiveSession"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
