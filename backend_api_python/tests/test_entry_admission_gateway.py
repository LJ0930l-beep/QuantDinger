"""Pure contracts for Canonical Entry V2 admission orchestration."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import inspect
import unittest
from uuid import UUID

from tests.pr12_contract_loader import load_pr12_gateway


modules = load_pr12_gateway()
admission = modules.admission
decimal = modules.decimal
entry = modules.entry
entry_v2 = modules.entry_v2
durable_entry = modules.durable_entry
durable_risk = modules.durable_risk
order = modules.order
outbox_repository = modules.outbox_repository
gateway_module = modules.gateway


_COMMAND_ID = UUID("00000000-0000-0000-0000-000000000001")
_ORDER_ID = UUID("00000000-0000-0000-0000-000000000002")


def _intent(action: object, execution: object = entry.ExecutionKind.MARKET) -> object:
    if action is order.OrderAction.CANCEL:
        return entry_v2.CanonicalEconomicIntentV2(
            cancel_target_kind=entry_v2.CancelTargetKind.CLIENT_ORDER_ID,
            cancel_target_id="client-order-1",
        )
    common = {
        "side": entry.OrderSide.BUY,
        "execution_kind": execution,
        "limit_price": decimal.Price("100") if execution in {entry.ExecutionKind.LIMIT, entry.ExecutionKind.STOP_LIMIT} else None,
        "trigger_price": decimal.Price("99") if execution in {entry.ExecutionKind.STOP_MARKET, entry.ExecutionKind.STOP_LIMIT} else None,
        "trigger_direction": entry_v2.TriggerDirection.AT_OR_BELOW if execution in {entry.ExecutionKind.STOP_MARKET, entry.ExecutionKind.STOP_LIMIT} else None,
        "trigger_price_type": entry_v2.TriggerPriceType.MARK if execution in {entry.ExecutionKind.STOP_MARKET, entry.ExecutionKind.STOP_LIMIT} else None,
    }
    if action in {
        order.OrderAction.REDUCE,
        order.OrderAction.CLOSE,
        order.OrderAction.EMERGENCY_CLOSE,
        order.OrderAction.PROTECTION,
    }:
        return entry_v2.CanonicalEconomicIntentV2(
            **common,
            reduce_only=True,
            target_position_id="position-1",
            close_quantity=decimal.Quantity("1"),
        )
    return entry_v2.CanonicalEconomicIntentV2(
        **common,
        quantity=decimal.Quantity("1"),
        quantity_semantics=entry_v2.QuantitySemantics.ABSOLUTE,
    )


def graph(
    *,
    action: object = order.OrderAction.OPEN,
    mode: object = entry.EntryMode.PAPER,
    execution: object = entry.ExecutionKind.MARKET,
    source: object = entry.EntrySource.REST,
    actor_type: object = order.Actor.HUMAN,
) -> object:
    protection = action is order.OrderAction.PROTECTION
    actor = entry.EntryActorContext(
        order.Actor.PROTECTION if protection else actor_type,
        "protection-1" if protection else "human-1",
        entry.EntrySource.PROTECTION if protection else source,
    )
    effect = (
        order.RiskEffect.NEUTRAL
        if action is order.OrderAction.CANCEL
        else order.RiskEffect.INCREASE_RISK
        if action in {order.OrderAction.OPEN, order.OrderAction.INCREASE}
        else order.RiskEffect.REDUCE_RISK
    )
    specification = entry_v2.CanonicalEntryRequestV2(
        tenant_id=1,
        credential_id=2,
        account_scope="account-1",
        instrument_id="BTCUSDT",
        market_type="swap",
        action=action,
        economic_intent=_intent(action, execution),
        actor=actor,
        risk_effect=effect,
        idempotency_key="case-1",
        correlation_id="correlation-1",
        occurred_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        mode=mode,
    )
    subject = (
        entry_v2.CancelTargetSubject(
            entry_v2.CancelTargetKind.CLIENT_ORDER_ID,
            "client-order-1",
        )
        if action is order.OrderAction.CANCEL
        else entry_v2.EconomicOrderSubject(_ORDER_ID)
    )
    return entry_v2.DurableEntryGraphV2(_COMMAND_ID, specification, subject)


def entry_receipt(value: object, disposition: object = durable_entry.DurableEntryPersistDisposition.CREATED) -> object:
    expected_order = value.subject.economic_order_id if isinstance(value.subject, entry_v2.EconomicOrderSubject) else None
    return durable_entry.DurableEntryPersistResult(
        value.command_id,
        value.specification.action,
        value.subject,
        expected_order,
        value.specification.economic_fingerprint,
        value.specification.request_fingerprint,
        disposition,
    )


def risk_receipt(
    value: object,
    *,
    allowed: bool = True,
    reservation_id: str | None = "00000000-0000-0000-0000-000000000003",
    disposition: object = durable_risk.DurableRiskPersistDisposition.CREATED,
) -> object:
    specification = value.specification
    status = "ALLOW" if allowed else "DENY"
    return durable_risk.DurableRiskPersistResultV2(
        value.command_id,
        value.subject.economic_order_id,
        durable_entry.DURABLE_ENTRY_CONTRACT_VERSION,
        specification.economic_fingerprint,
        specification.request_fingerprint,
        specification.tenant_id,
        specification.credential_id,
        specification.account_scope,
        specification.instrument_id,
        specification.market_type,
        specification.action,
        specification.risk_effect,
        specification.actor.actor_type.value,
        specification.actor.actor_id,
        specification.actor.entry_source.value,
        specification.mode.value,
        specification.correlation_id,
        specification.occurred_at,
        "a" * 64,
        "b" * 64,
        "00000000-0000-0000-0000-000000000004",
        reservation_id if allowed else None,
        allowed,
        status,
        "c" * 64,
        disposition,
    )


class DurableEntries:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[object, object]] = []

    def persist_durable_entry(self, connection: object, value: object) -> object:
        self.calls.append((connection, value))
        return self.result


class DurableRisk:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[object, object]] = []

    def evaluate_and_persist(self, connection: object, value: object) -> object:
        self.calls.append((connection, value))
        return self.result


class AdmissionOutbox:
    def __init__(self, result: object | None = None) -> None:
        self.result = result
        self.calls: list[tuple[object, object, object, object]] = []

    def persist_admission(self, connection: object, value: object, durable: object, risk: object) -> object:
        self.calls.append((connection, value, durable, risk))
        if self.result is not None:
            return self.result
        event = admission.deterministic_admission_outbox_event(value, risk_result=risk)
        return outbox_repository.OutboxPersistResult(
            event,
            outbox_repository.OutboxPersistDisposition.CREATED,
        )


class EntryAdmissionGatewayTests(unittest.TestCase):
    def _gateway(
        self,
        value: object,
        *,
        durable: object | None = None,
        risk: object | None = None,
        outbox: object | None = None,
    ) -> tuple[object, DurableEntries, DurableRisk, AdmissionOutbox]:
        durable_port = DurableEntries(durable or entry_receipt(value))
        risk_port = DurableRisk(
            risk
            if risk is not None
            else None
            if value.specification.action is order.OrderAction.CANCEL
            else risk_receipt(value)
        )
        outbox_port = AdmissionOutbox(outbox)
        return (
            gateway_module.CanonicalEntryAdmissionGateway(
                durable_entries=durable_port,
                durable_risk=risk_port,
                outbox=outbox_port,
            ),
            durable_port,
            risk_port,
            outbox_port,
        )

    def test_disabled_returns_typed_receipt_without_opening_any_port(self) -> None:
        value = graph(mode=entry.EntryMode.DISABLED)
        gateway, durable_port, risk_port, outbox_port = self._gateway(value)
        result = gateway.admit(object(), value)
        self.assertIs(result.disposition, admission.EntryAdmissionDisposition.DISABLED)
        self.assertEqual((durable_port.calls, risk_port.calls, outbox_port.calls), ([], [], []))

    def test_restricted_sources_remain_disabled_without_persistence(self) -> None:
        for source, actor_type in (
            (entry.EntrySource.AGENT, order.Actor.AGENT),
            (entry.EntrySource.MCP, order.Actor.MCP),
            (entry.EntrySource.GRID, order.Actor.GRID),
        ):
            with self.subTest(source=source):
                value = graph(mode=None, source=source, actor_type=actor_type)
                self.assertIs(value.specification.mode, entry.EntryMode.DISABLED)
                gateway, durable_port, risk_port, outbox_port = self._gateway(value)
                self.assertIs(
                    gateway.admit(object(), value).disposition,
                    admission.EntryAdmissionDisposition.DISABLED,
                )
                self.assertEqual((durable_port.calls, risk_port.calls, outbox_port.calls), ([], [], []))

    def test_cancel_persists_entry_and_outbox_without_risk_or_economic_order(self) -> None:
        value = graph(action=order.OrderAction.CANCEL)
        gateway, durable_port, risk_port, outbox_port = self._gateway(value)
        result = gateway.admit(object(), value)
        self.assertIs(result.disposition, admission.EntryAdmissionDisposition.CREATED)
        self.assertIsNone(result.economic_order_id)
        self.assertIsNone(result.risk_decision_id)
        self.assertIsNone(result.reservation_id)
        self.assertEqual((len(durable_port.calls), len(risk_port.calls), len(outbox_port.calls)), (1, 0, 1))

    def test_open_and_increase_require_allowed_reservation(self) -> None:
        for action in (order.OrderAction.OPEN, order.OrderAction.INCREASE):
            with self.subTest(action=action):
                value = graph(action=action)
                gateway, _, _, outbox_port = self._gateway(
                    value,
                    risk=risk_receipt(value, reservation_id=None),
                )
                with self.assertRaises(admission.EntryAdmissionConflict):
                    gateway.admit(object(), value)
                self.assertEqual(outbox_port.calls, [])

    def test_denied_open_is_terminal_without_reservation_or_outbox(self) -> None:
        value = graph()
        gateway, durable_port, risk_port, outbox_port = self._gateway(
            value,
            risk=risk_receipt(value, allowed=False),
        )
        result = gateway.admit(object(), value)
        self.assertIs(result.disposition, admission.EntryAdmissionDisposition.RISK_REJECTED)
        self.assertEqual((len(durable_port.calls), len(risk_port.calls), len(outbox_port.calls)), (1, 1, 0))
        self.assertIsNone(result.reservation_id)

    def test_reducing_actions_require_allow_without_reservation(self) -> None:
        for action in (
            order.OrderAction.REDUCE,
            order.OrderAction.CLOSE,
            order.OrderAction.EMERGENCY_CLOSE,
            order.OrderAction.PROTECTION,
        ):
            with self.subTest(action=action):
                value = graph(action=action)
                gateway, _, _, outbox_port = self._gateway(
                    value,
                    risk=risk_receipt(value, reservation_id=None),
                )
                result = gateway.admit(object(), value)
                self.assertIs(result.disposition, admission.EntryAdmissionDisposition.CREATED)
                self.assertEqual(len(outbox_port.calls), 1)
                invalid, _, _, invalid_outbox = self._gateway(value, risk=risk_receipt(value))
                with self.assertRaises(admission.EntryAdmissionConflict):
                    invalid.admit(object(), value)
                self.assertEqual(invalid_outbox.calls, [])

    def test_exact_replay_and_any_created_receipt_are_distinct(self) -> None:
        value = graph()
        replay_entry = entry_receipt(value, durable_entry.DurableEntryPersistDisposition.REPLAYED)
        replay_risk = risk_receipt(
            value,
            disposition=durable_risk.DurableRiskPersistDisposition.REPLAYED,
        )
        replay_event = admission.deterministic_admission_outbox_event(value, risk_result=replay_risk)
        replay_outbox = outbox_repository.OutboxPersistResult(
            replay_event,
            outbox_repository.OutboxPersistDisposition.REPLAYED,
        )
        gateway, _, _, _ = self._gateway(value, durable=replay_entry, risk=replay_risk, outbox=replay_outbox)
        self.assertIs(gateway.admit(object(), value).disposition, admission.EntryAdmissionDisposition.REPLAYED)
        created_risk = replace(replay_risk, disposition=durable_risk.DurableRiskPersistDisposition.CREATED)
        created_event = admission.deterministic_admission_outbox_event(value, risk_result=created_risk)
        created_outbox = outbox_repository.OutboxPersistResult(
            created_event,
            outbox_repository.OutboxPersistDisposition.REPLAYED,
        )
        gateway, _, _, _ = self._gateway(value, durable=replay_entry, risk=created_risk, outbox=created_outbox)
        self.assertIs(gateway.admit(object(), value).disposition, admission.EntryAdmissionDisposition.CREATED)

    def test_cancel_replay_uses_only_its_relevant_receipts(self) -> None:
        value = graph(action=order.OrderAction.CANCEL)
        replay_entry = entry_receipt(value, durable_entry.DurableEntryPersistDisposition.REPLAYED)
        replay_event = admission.deterministic_admission_outbox_event(value, risk_result=None)
        replay_outbox = outbox_repository.OutboxPersistResult(
            replay_event,
            outbox_repository.OutboxPersistDisposition.REPLAYED,
        )
        gateway, _, risk_port, _ = self._gateway(value, durable=replay_entry, outbox=replay_outbox)
        self.assertIs(gateway.admit(object(), value).disposition, admission.EntryAdmissionDisposition.REPLAYED)
        self.assertEqual(risk_port.calls, [])

    def test_receipt_identity_mismatches_fail_closed_before_next_port(self) -> None:
        value = graph()
        invalid_entry = replace(entry_receipt(value), request_fingerprint="d" * 64)
        gateway, _, risk_port, outbox_port = self._gateway(value, durable=invalid_entry)
        with self.assertRaises(admission.EntryAdmissionConflict):
            gateway.admit(object(), value)
        self.assertEqual((risk_port.calls, outbox_port.calls), ([], []))
        invalid_risk = replace(risk_receipt(value), actor_id="other")
        gateway, _, _, outbox_port = self._gateway(value, risk=invalid_risk)
        with self.assertRaises(admission.EntryAdmissionConflict):
            gateway.admit(object(), value)
        self.assertEqual(outbox_port.calls, [])
        wrong_event = admission.deterministic_admission_outbox_event(value, risk_result=risk_receipt(value))
        invalid_outbox = outbox_repository.OutboxPersistResult(
            replace(wrong_event, aggregate_version=1),
            outbox_repository.OutboxPersistDisposition.CREATED,
        )
        gateway, _, _, _ = self._gateway(value, outbox=invalid_outbox)
        with self.assertRaises(admission.EntryAdmissionConflict):
            gateway.admit(object(), value)

    def test_all_ports_receive_the_same_caller_connection(self) -> None:
        value, connection = graph(), object()
        gateway, durable_port, risk_port, outbox_port = self._gateway(value)
        gateway.admit(connection, value)
        self.assertIs(durable_port.calls[0][0], connection)
        self.assertIs(risk_port.calls[0][0], connection)
        self.assertIs(outbox_port.calls[0][0], connection)

    def test_gateway_never_owns_a_transaction_or_legacy_graph(self) -> None:
        source = inspect.getsource(gateway_module).lower()
        for forbidden in (
            ".commit(",
            ".rollback(",
            ".cursor(",
            "commandgraph",
            "orderintent",
            "canonicalcommanddraft",
            "persist_reservation",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
