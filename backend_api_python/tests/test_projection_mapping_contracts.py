from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4
import unittest


def _load_contracts():
    root = Path(__file__).resolve().parents[1]
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts", "app.domain.canonical_entry_v2_contracts",
        "app.domain.durable_entry_persistence_contracts", "app.domain.hard_risk_contracts",
        "app.domain.durable_risk_enforcement_v2_contracts",
        "app.domain.authoritative_risk_facts_contracts", "app.domain.outbox_projection_contracts",
        "app.domain.entry_admission_v2_contracts", "app.domain.projection_mapping_contracts",
    )
    missing = object()
    original = {name: sys.modules.get(name, missing) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(root / "app")]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(root / "app" / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        modules = {}
        for name in names[2:]:
            path = root / "app" / "domain" / f"{name.rsplit('.', 1)[-1]}.py"
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load {name}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name.rsplit('.', 1)[-1]] = module
        return SimpleNamespace(**modules)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


m = _load_contracts()
OutboxEvent = m.outbox_projection_contracts.OutboxEvent
OutboxProjectionContractError = m.outbox_projection_contracts.OutboxProjectionContractError
OrderAction = m.order_contracts.OrderAction
RiskEffect = m.order_contracts.RiskEffect
ProjectionMappingError = m.projection_mapping_contracts.ProjectionMappingError
ProjectionSubjectKind = m.projection_mapping_contracts.ProjectionSubjectKind
map_event = m.projection_mapping_contracts.map_admission_outbox_to_projection


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
COMMAND_ID = str(uuid4())
ORDER_ID = str(uuid4())
RISK_ID = str(uuid4())
RESERVATION_ID = str(uuid4())


def _hash(seed: str) -> str:
    return (seed * 64)[:64]


def _payload(action: OrderAction = OrderAction.OPEN, *, correlation_id: str = "corr-1") -> dict:
    cancel = action is OrderAction.CANCEL
    increasing = action in (OrderAction.OPEN, OrderAction.INCREASE)
    return {
        "admission_contract_version": "entry-admission-v2",
        "command_id": COMMAND_ID,
        "action": action.value,
        "risk_effect": (
            RiskEffect.NEUTRAL.value
            if cancel
            else RiskEffect.INCREASE_RISK.value if increasing else RiskEffect.REDUCE_RISK.value
        ),
        "subject_kind": "CANCEL_TARGET" if cancel else "ECONOMIC_ORDER",
        "subject_id": "client-123" if cancel else ORDER_ID,
        "cancel_target_kind": "CLIENT_ORDER_ID" if cancel else None,
        "economic_order_id": None if cancel else ORDER_ID,
        "economic_fingerprint": _hash("a"),
        "request_fingerprint": _hash("b"),
        "tenant_id": 7,
        "credential_id": 8,
        "account_scope": "paper-main",
        "instrument_id": "BTC-USDT",
        "market_type": "swap",
        "actor_type": "STRATEGY",
        "actor_id": "strategy-1",
        "source": "STRATEGY",
        "mode": "PAPER",
        "correlation_id": correlation_id,
        "occurred_at": NOW.isoformat(),
        "risk_decision_id": None if cancel else RISK_ID,
        "risk_decision_status": None if cancel else "ALLOW",
        "decision_fingerprint": None if cancel else _hash("c"),
        "scope_fingerprint": None if cancel else _hash("d"),
        "audit_fingerprint": None if cancel else _hash("e"),
        "reservation_id": None if cancel or not increasing else RESERVATION_ID,
    }


def _event(action: OrderAction = OrderAction.OPEN, *, correlation_id: str = "corr-1") -> OutboxEvent:
    cancel = action is OrderAction.CANCEL
    return OutboxEvent(
        "DURABLE_ENTRY_COMMAND" if cancel else "DURABLE_ECONOMIC_ORDER",
        COMMAND_ID if cancel else ORDER_ID,
        0,
        "DURABLE_CANCEL_ADMITTED" if cancel else "DURABLE_ENTRY_ADMITTED",
        "entry-admission-v2",
        _payload(action, correlation_id=correlation_id),
    )


class ProjectionMappingTests(unittest.TestCase):
    def test_open_mapping_preserves_scope_risk_reservation_and_source_evidence(self):
        event = _event()
        result = map_event(event)
        self.assertEqual(result.subject_kind, ProjectionSubjectKind.ECONOMIC_ORDER)
        self.assertEqual(result.subject_id, ORDER_ID)
        self.assertEqual(result.economic_order_id, ORDER_ID)
        self.assertEqual(result.risk_effect, RiskEffect.INCREASE_RISK)
        self.assertEqual(result.reservation_id, RESERVATION_ID)
        self.assertEqual(result.tenant_id, 7)
        self.assertEqual(result.instrument_id, "BTC-USDT")
        self.assertEqual(result.payload_hash, event.payload_hash)
        self.assertEqual(result.canonical_payload, event.canonical_payload)

    def test_cancel_mapping_has_typed_cancel_scope_and_no_economic_order(self):
        result = map_event(_event(OrderAction.CANCEL))
        self.assertEqual(result.subject_kind, ProjectionSubjectKind.CANCEL_TARGET)
        self.assertEqual(result.subject_id, "client-123")
        self.assertIsNone(result.economic_order_id)
        self.assertIsNone(result.reservation_id)
        self.assertIs(result.risk_effect, RiskEffect.NEUTRAL)

    def test_reduce_mapping_preserves_scope_and_rejects_reservation_fact(self):
        result = map_event(_event(OrderAction.REDUCE))
        self.assertEqual(result.subject_kind, ProjectionSubjectKind.ECONOMIC_ORDER)
        self.assertEqual(result.economic_order_id, ORDER_ID)
        self.assertIs(result.risk_effect, RiskEffect.REDUCE_RISK)
        self.assertIsNone(result.reservation_id)
        self.assertEqual(result.scope_fingerprint, _hash("d"))

    def test_same_event_replays_to_identical_fingerprint(self):
        event = _event()
        first = map_event(event)
        second = map_event(event)
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_source_payload_change_changes_fingerprint_without_dropping_facts(self):
        first = map_event(_event(correlation_id="corr-1"))
        second = map_event(_event(correlation_id="corr-2"))
        self.assertNotEqual(first.payload_hash, second.payload_hash)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.economic_fingerprint, second.economic_fingerprint)

    def test_unknown_schema_and_hash_tamper_fail_closed(self):
        unknown = OutboxEvent("DURABLE_ECONOMIC_ORDER", ORDER_ID, 0, "DURABLE_ENTRY_ADMITTED", "future-v9", _payload())
        with self.assertRaises(ProjectionMappingError):
            map_event(unknown)
        tampered = _event()
        object.__setattr__(tampered, "payload_hash", "0" * 64)
        with self.assertRaises(ProjectionMappingError):
            map_event(tampered)

    def test_float_payload_is_rejected_before_mapping(self):
        payload = _payload()
        payload["tenant_id"] = 7.0
        with self.assertRaises(OutboxProjectionContractError):
            OutboxEvent("DURABLE_ECONOMIC_ORDER", ORDER_ID, 0, "DURABLE_ENTRY_ADMITTED", "entry-admission-v2", payload)

    def test_mapper_rejects_non_event_input_with_typed_error(self):
        with self.assertRaises(ProjectionMappingError):
            map_event(object())

    def test_domain_mapping_module_has_no_infrastructure_imports(self):
        path = Path(__file__).resolve().parents[1] / "app" / "domain" / "projection_mapping_contracts.py"
        source = path.read_text(encoding="utf-8").lower()
        for forbidden in ("flask", "psycopg", "exchange", "worker", "scheduler", "executor", "runtime"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
