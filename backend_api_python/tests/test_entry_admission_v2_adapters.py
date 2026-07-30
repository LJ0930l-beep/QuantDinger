"""Focused caller-owned adapter tests for Canonical Entry V2 admission."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest
from uuid import uuid4

from tests.pr12c_admission_loader import load_pr12c_admission


m = load_pr12c_admission()
OrderAction = m.order.OrderAction
Actor = m.order.Actor
RiskEffect = m.order.RiskEffect
EntryActorContext = m.entry.EntryActorContext
EntrySource = m.entry.EntrySource
EntryMode = m.entry.EntryMode
ExecutionKind = m.entry.ExecutionKind
OrderSide = m.entry.OrderSide
PositionSide = m.entry.PositionSide
Quantity = m.decimal.Quantity
QuoteAmount = m.decimal.QuoteAmount


def graph(action: OrderAction = OrderAction.OPEN):
    reducing = action in {
        OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION,
    }
    if action is OrderAction.CANCEL:
        intent = m.entry_v2.CanonicalEconomicIntentV2(
            cancel_target_kind=m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID,
            cancel_target_id="venue-client-1",
        )
        subject = m.entry_v2.CancelTargetSubject(
            m.entry_v2.CancelTargetKind.CLIENT_ORDER_ID, "venue-client-1",
        )
        actor = EntryActorContext(Actor.HUMAN, "human-1", EntrySource.REST)
        effect = RiskEffect.NEUTRAL
    else:
        intent = m.entry_v2.CanonicalEconomicIntentV2(
            side=OrderSide.BUY,
            quantity=None if reducing else Quantity("1"),
            quantity_semantics=None if reducing else m.entry_v2.QuantitySemantics.ABSOLUTE,
            execution_kind=ExecutionKind.MARKET,
            reduce_only=reducing,
            target_position_id="position-1" if reducing else None,
            close_quantity=Quantity("1") if reducing else None,
            position_side=PositionSide.NET,
        )
        protection = action is OrderAction.PROTECTION
        actor = EntryActorContext(
            Actor.PROTECTION if protection else Actor.HUMAN,
            "protection-1" if protection else "human-1",
            EntrySource.PROTECTION if protection else EntrySource.REST,
        )
        effect = RiskEffect.REDUCE_RISK if reducing else RiskEffect.INCREASE_RISK
        subject = m.entry_v2.EconomicOrderSubject("22222222-2222-2222-2222-222222222222")
    specification = m.entry_v2.CanonicalEntryRequestV2(
        1, 2, "account-1", "BTCUSDT", "swap", action, intent, actor, effect,
        "case-1", "corr-1", datetime(2026, 7, 29, tzinfo=timezone.utc), EntryMode.PAPER,
    )
    return m.entry_v2.DurableEntryGraphV2(
        "11111111-1111-1111-1111-111111111111", specification, subject,
    )


def policy():
    return m.hard_risk.RiskLimitPolicy(
        "policy-1", "USDT", QuoteAmount("1000"), QuoteAmount("700"),
        QuoteAmount("600"), "4", QuoteAmount("100"), QuoteAmount("100"), "0.20",
    )


def exposure():
    return m.hard_risk.RiskExposureSnapshot(
        "account-1", "BTCUSDT", "USDT", "100", "100", "100", "800", "500", "500", "0",
        m.order.ReconciliationHealth.HEALTHY, m.hard_risk.MarketDataHealth.FRESH, True,
    )


def switches(*, enabled=False):
    state = m.hard_risk.KillSwitchState(
        1,
        enabled,
        m.hard_risk.KillSwitchMode.OPEN_BLOCKED if enabled else None,
    )
    return m.hard_risk.KillSwitchSnapshot(state, state, state)


def request(action: OrderAction):
    increasing = action in (OrderAction.OPEN, OrderAction.INCREASE)
    return m.hard_risk.HardRiskRequest(
        action,
        Actor.PROTECTION if action is OrderAction.PROTECTION else Actor.HUMAN,
        RiskEffect.REDUCE_RISK if action is OrderAction.PROTECTION else None,
        "100" if increasing else "0", "100" if increasing else "0",
        "100" if increasing else "0", "25" if increasing else "0",
    )


def inputs(value, *, reservation=True, denied=False):
    demand = None
    if reservation:
        demand = m.hard_risk.RiskReservationDemand(
            "provider-demand", "account-1", "BTCUSDT", "USDT", "100", "100", "100", "25",
        )
    provenance = m.authoritative_risk_facts.RiskFactProvenance(
        m.authoritative_risk_facts.RiskFactSourceKind.POLICY,
        "test-policy", "v1", "a" * 64, datetime(2026, 7, 29, tzinfo=timezone.utc), 60,
    )
    return m.admission.DurableRiskAdmissionInputs(
        policy(), exposure(), switches(enabled=denied), request(value.specification.action),
        datetime(2026, 7, 29, tzinfo=timezone.utc), reservation_demand=demand, provenance=(provenance,),
    )


def durable_entry_result(value, disposition=None):
    return m.durable_entry.DurableEntryPersistResult(
        value.command_id, value.specification.action, value.subject,
        value.subject.economic_order_id if isinstance(value.subject, m.entry_v2.EconomicOrderSubject) else None,
        value.specification.economic_fingerprint, value.specification.request_fingerprint,
        disposition or m.durable_entry.DurableEntryPersistDisposition.CREATED,
    )


class _Provider:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def prepare(self, connection, value):
        self.calls.append((connection, value))
        return self.outcome


class _RiskRepository:
    def __init__(self, disposition=None):
        self.calls = []
        self.disposition = disposition or m.durable_risk.DurableRiskPersistDisposition.CREATED

    def load_complete_replay(self, connection, value):
        return None

    def persist_durable_risk(self, connection, *, policy_snapshot, input_snapshot, decision, reservation, provenance=()):
        self.calls.append((connection, policy_snapshot, input_snapshot, decision, reservation, provenance))
        return m.risk_repository.DurableRiskEnforcementRepositoryV2()._result(
            decision, reservation, self.disposition,
        )


class _OutboxRepository:
    def __init__(self, disposition=None, replacement=None):
        self.calls = []
        self.disposition = disposition or m.outbox_repository.OutboxPersistDisposition.CREATED
        self.replacement = replacement

    def persist_event(self, connection, event, *, available_at):
        self.calls.append((connection, event, available_at))
        return m.outbox_repository.OutboxPersistResult(
            self.replacement or event, self.disposition,
        )


class EntryAdmissionV2AdapterTests(unittest.TestCase):
    def test_allowed_increase_builds_single_reservation_and_never_controls_transaction(self):
        value = graph()
        provider = _Provider(inputs(value, reservation=True))
        repository = _RiskRepository()
        connection = object()
        result = m.adapters.DurableRiskAdmissionAdapter(provider=provider, repository=repository).evaluate_and_persist(connection, value)
        self.assertTrue(result.allowed)
        self.assertIsNotNone(result.reservation_id)
        self.assertEqual(1, len(repository.calls))
        self.assertIsNotNone(repository.calls[0][-1])
        self.assertEqual([(connection, value)], provider.calls)

    def test_denied_increase_persists_decision_without_reservation(self):
        value = graph()
        provider = _Provider(inputs(value, reservation=True, denied=True))
        repository = _RiskRepository()
        result = m.adapters.DurableRiskAdmissionAdapter(provider=provider, repository=repository).evaluate_and_persist(object(), value)
        self.assertFalse(result.allowed)
        self.assertIsNone(result.reservation_id)
        self.assertIsNone(repository.calls[0][-2])

    def test_reducing_action_rejects_reservation_demand_before_repository(self):
        value = graph(OrderAction.CLOSE)
        provider = _Provider(inputs(value, reservation=True))
        repository = _RiskRepository()
        with self.assertRaises(m.admission.EntryAdmissionConflict):
            m.adapters.DurableRiskAdmissionAdapter(provider=provider, repository=repository).evaluate_and_persist(object(), value)
        self.assertEqual([], repository.calls)

    def test_every_reducing_action_persists_without_reservation(self):
        for action in (
            OrderAction.REDUCE, OrderAction.CLOSE,
            OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION,
        ):
            with self.subTest(action=action):
                value = graph(action)
                repository = _RiskRepository()
                result = m.adapters.DurableRiskAdmissionAdapter(
                    provider=_Provider(inputs(value, reservation=False)), repository=repository,
                ).evaluate_and_persist(object(), value)
                self.assertTrue(result.allowed)
                self.assertIsNone(result.reservation_id)
                self.assertIsNone(repository.calls[0][-2])

    def test_allowed_increase_without_demand_fails_before_repository(self):
        value = graph()
        repository = _RiskRepository()
        with self.assertRaises(m.admission.EntryAdmissionConflict):
            m.adapters.DurableRiskAdmissionAdapter(
                provider=_Provider(inputs(value, reservation=False)), repository=repository,
            ).evaluate_and_persist(object(), value)
        self.assertEqual([], repository.calls)

    def test_cancel_bypasses_risk_adapter_before_provider(self):
        provider = _Provider(object())
        repository = _RiskRepository()
        with self.assertRaises(m.admission.EntryAdmissionConflict):
            m.adapters.DurableRiskAdmissionAdapter(provider=provider, repository=repository).evaluate_and_persist(object(), graph(OrderAction.CANCEL))
        self.assertEqual([], provider.calls)
        self.assertEqual([], repository.calls)

    def test_outbox_event_is_deterministic_typed_and_uses_occurred_at(self):
        value = graph()
        risk_provider = _Provider(inputs(value, reservation=True))
        risk = m.adapters.DurableRiskAdmissionAdapter(provider=risk_provider, repository=_RiskRepository()).evaluate_and_persist(object(), value)
        repository = _OutboxRepository()
        result = m.adapters.AdmissionOutboxAdapter(repository=repository).persist_admission(
            object(), value, durable_entry_result(value), risk,
        )
        self.assertEqual(m.outbox_repository.OutboxPersistDisposition.CREATED, result.disposition)
        _, event, available_at = repository.calls[0]
        self.assertEqual(value.specification.occurred_at, available_at)
        self.assertEqual("DURABLE_ECONOMIC_ORDER", event.aggregate_type)
        self.assertEqual(value.subject.economic_order_id, event.aggregate_id)
        self.assertEqual(m.admission.ENTRY_ADMISSION_EVENT_TYPE, event.event_type)
        self.assertEqual(event, m.admission.deterministic_admission_outbox_event(value, risk_result=risk))
        self.assertNotIn("CREATED", event.canonical_payload)
        self.assertNotIn("REPLAYED", event.canonical_payload)

    def test_cancel_outbox_uses_command_aggregate_and_never_carries_risk(self):
        value = graph(OrderAction.CANCEL)
        repository = _OutboxRepository()
        m.adapters.AdmissionOutboxAdapter(repository=repository).persist_admission(
            object(), value, durable_entry_result(value), None,
        )
        _, event, _ = repository.calls[0]
        self.assertEqual("DURABLE_ENTRY_COMMAND", event.aggregate_type)
        self.assertEqual(value.command_id, event.aggregate_id)
        self.assertEqual(m.admission.ENTRY_ADMISSION_CANCEL_EVENT_TYPE, event.event_type)
        with self.assertRaises(m.admission.EntryAdmissionConflict):
            m.adapters.AdmissionOutboxAdapter(repository=_OutboxRepository()).persist_admission(
                object(), value, durable_entry_result(value), object(),
            )

    def test_outbox_rejects_untyped_or_mismatched_receipt(self):
        value = graph()
        risk = m.adapters.DurableRiskAdmissionAdapter(
            provider=_Provider(inputs(value, reservation=True)), repository=_RiskRepository(),
        ).evaluate_and_persist(object(), value)
        with self.assertRaises(m.admission.EntryAdmissionConflict):
            m.adapters.AdmissionOutboxAdapter(repository=_OutboxRepository(replacement=uuid4())).persist_admission(
                object(), value, durable_entry_result(value), risk,
            )

    def test_raw_port_failures_are_reclassified_without_transaction_control(self):
        class RawRiskRepository:
            def persist_durable_risk(self, *args, **kwargs):
                raise RuntimeError("driver failure")

        value = graph()
        with self.assertRaises(m.admission.EntryAdmissionError):
            m.adapters.DurableRiskAdmissionAdapter(
                provider=_Provider(inputs(value, reservation=True)), repository=RawRiskRepository(),
            ).evaluate_and_persist(object(), value)

        class RawOutboxRepository:
            def persist_event(self, *args, **kwargs):
                raise RuntimeError("driver failure")

        risk = m.adapters.DurableRiskAdmissionAdapter(
            provider=_Provider(inputs(value, reservation=True)), repository=_RiskRepository(),
        ).evaluate_and_persist(object(), value)
        with self.assertRaises(m.admission.EntryAdmissionError):
            m.adapters.AdmissionOutboxAdapter(repository=RawOutboxRepository()).persist_admission(
                object(), value, durable_entry_result(value), risk,
            )

    def test_source_has_no_transaction_or_runtime_boundary(self):
        source = Path(m.adapters.__file__).read_text(encoding="utf-8")
        for forbidden in (
            ".commit(", ".rollback(", "from flask", "import flask",
            "app.services.live_trading", "app.services.trading_executor",
            "app.services.pending_order_worker", "datetime.now(",
        ):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
