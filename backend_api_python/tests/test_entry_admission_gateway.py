from __future__ import annotations

from datetime import datetime, timezone
import inspect
import unittest
from uuid import uuid4

from tests.pr12_contract_loader import load_pr12_gateway

modules = load_pr12_gateway()
c, d, o, ci, g = modules.canonical, modules.decimal, modules.order, modules.command, modules.gateway


def draft(mode, action=o.OrderAction.OPEN, source=c.EntrySource.REST, actor=o.Actor.HUMAN):
    if action is o.OrderAction.CANCEL:
        intent = c.CanonicalEconomicIntent(cancel_target_id="target-1")
    elif action in (o.OrderAction.REDUCE, o.OrderAction.CLOSE, o.OrderAction.EMERGENCY_CLOSE, o.OrderAction.PROTECTION):
        intent = c.CanonicalEconomicIntent(c.OrderSide.SELL, None, c.ExecutionKind.MARKET, reduce_only=True, target_position_id="position-1", close_quantity=d.Quantity("1"))
    else:
        intent = c.CanonicalEconomicIntent(c.OrderSide.BUY, d.Quantity("1"), c.ExecutionKind.MARKET)
    return c.CanonicalCommandDraft(c.CanonicalEntryRequest(1, 2, "account-a", "BTC-USDT", "usdm", action, intent, c.EntryActorContext(actor, "actor:1", source), "case-1", "corr-1", datetime(2026, 1, 1, tzinfo=timezone.utc), mode=mode))


def graph(value, *, actor_id="actor:1", tenant_id=None, credential_id=None, account_scope=None, instrument_id=None, market_type=None, action=None, source=None, idempotency_key=None, correlation_id=None, request_fingerprint=None, economic_fingerprint=None, intent_payload_hash=None, risk_effect=None):
    request = value.request
    command_id = uuid4()
    intent = ci.OrderIntent(
        intent_id=uuid4(), economic_order_id=uuid4(), command_id=command_id,
        tenant_id=request.tenant_id if tenant_id is None else tenant_id,
        credential_id=request.credential_id if credential_id is None else credential_id,
        account_scope=request.account_scope if account_scope is None else account_scope,
        exchange_id="binance", instrument_id=request.instrument_id if instrument_id is None else instrument_id,
        market_type=request.market_type if market_type is None else market_type, side="BUY",
        target_quantity=d.Quantity("1"), instrument_rule_snapshot_id=uuid4(), instrument_rule_version="rule-v1",
        order_type="MARKET", execution_algo="NONE", rounding_mode="DOWN",
    )
    payload = {
        "canonical_request_fingerprint": request.request_fingerprint if request_fingerprint is None else request_fingerprint,
        "economic_fingerprint": request.economic_fingerprint if economic_fingerprint is None else economic_fingerprint,
        "intent_payload_hash": intent.payload_hash if intent_payload_hash is None else intent_payload_hash,
        "risk_effect": request.risk_effect.value if risk_effect is None else risk_effect,
    }
    command = ci.OrderCommand(
        command_id=command_id, tenant_id=request.tenant_id if tenant_id is None else tenant_id, user_id=1,
        credential_id=request.credential_id if credential_id is None else credential_id,
        actor_type=request.actor.actor_type, actor_id=actor_id,
        source=request.actor.entry_source.value.lower() if source is None else source,
        action=request.action if action is None else action,
        account_scope=request.account_scope if account_scope is None else account_scope,
        request_payload=payload, idempotency_key=request.idempotency_key if idempotency_key is None else idempotency_key,
        correlation_id=request.correlation_id if correlation_id is None else correlation_id,
    )
    return ci.CommandGraph(command, intent)


class Mapper:
    def __init__(self, result): self.result, self.calls = result, []
    def map(self, value): self.calls.append(value); return self.result


class CommandPort:
    def __init__(self, result): self.result, self.calls = result, []
    def persist_command_graph(self, connection, value): self.calls.append((connection, value)); return self.result


class RiskPort:
    def __init__(self, result): self.result, self.calls = result, []
    def persist_for_admission(self, connection, value, graph_value): self.calls.append((connection, value, graph_value)); return self.result


class OutboxPort:
    def __init__(self, result): self.result, self.calls = result, []
    def persist_admission(self, connection, value, graph_value): self.calls.append((connection, value, graph_value)); return self.result


def command(replayed=False):
    return ci.CommandGraphResult("c", "i", "e", o.EconomicOrderState.CREATED, ci.CommandGraphDisposition.REPLAYED if replayed else ci.CommandGraphDisposition.CREATED)


def allowed(*, replayed=False, reservation=True):
    receipt = None if not reservation else g.ReservationPersistResult("reservation-1", g.ReservationDisposition.REPLAYED if replayed else g.ReservationDisposition.CREATED)
    return g.HardRiskPersistResult(True, receipt, g.HardRiskDisposition.REPLAYED if replayed else g.HardRiskDisposition.CREATED)


class EntryAdmissionGatewayTests(unittest.TestCase):
    def gateway(self, value, *, command_replayed=False, risk=None, outbox_replayed=False, graph_value=None):
        return g.CanonicalEntryAdmissionGateway(
            mapper=Mapper(graph(value) if graph_value is None else graph_value),
            command_graphs=CommandPort(command(command_replayed)),
            hard_risk=RiskPort(allowed() if risk is None else risk),
            outbox=OutboxPort(g.OutboxPersistResult("event-1", g.OutboxDisposition.REPLAYED if outbox_replayed else g.OutboxDisposition.CREATED)),
        )

    def test_mapper_full_identity_mismatch_fails_before_every_persistence_port(self):
        value = draft(c.EntryMode.PAPER)
        for field, changed in (
            ("tenant_id", 3), ("credential_id", 4), ("account_scope", "other"), ("instrument_id", "ETH-USDT"),
            ("market_type", "spot"), ("action", o.OrderAction.INCREASE), ("actor_id", "actor-2"), ("source", "manual"),
            ("idempotency_key", "case-2"), ("correlation_id", "corr-2"), ("request_fingerprint", "0" * 64),
            ("economic_fingerprint", "1" * 64), ("intent_payload_hash", "2" * 64), ("risk_effect", o.RiskEffect.REDUCE_RISK.value),
        ):
            with self.subTest(field=field):
                mapped = graph(value, **{field: changed})
                mapper, commands, risks, outbox = Mapper(mapped), CommandPort(command()), RiskPort(allowed()), OutboxPort(g.OutboxPersistResult("event-1", g.OutboxDisposition.CREATED))
                gate = g.CanonicalEntryAdmissionGateway(mapper=mapper, command_graphs=commands, hard_risk=risks, outbox=outbox)
                with self.assertRaises(g.EntryAdmissionConflict): gate.admit(object(), value)
                self.assertEqual((len(commands.calls), len(risks.calls), len(outbox.calls)), (0, 0, 0))

    def test_disabled_and_rejected_zero_calls(self):
        value = draft(c.EntryMode.DISABLED)
        mapper, commands, risks, outbox = Mapper(graph(value)), CommandPort(command()), RiskPort(allowed()), OutboxPort(g.OutboxPersistResult("event-1", g.OutboxDisposition.CREATED))
        gate = g.CanonicalEntryAdmissionGateway(mapper=mapper, command_graphs=commands, hard_risk=risks, outbox=outbox)
        self.assertEqual(gate.admit(object(), value).disposition, g.EntryAdmissionDisposition.DISABLED)
        rejected = c.CanonicalCommandDraft(draft(c.EntryMode.PAPER).request, c.EntryDisposition.REJECTED, c.EntryRejection.MISSING_FACT)
        result = gate.admit(object(), rejected)
        self.assertEqual((result.disposition, result.rejection), (g.EntryAdmissionDisposition.REJECTED, c.EntryRejection.MISSING_FACT))
        self.assertEqual((len(mapper.calls), len(commands.calls), len(risks.calls), len(outbox.calls)), (0, 0, 0, 0))

    def test_deny_preserves_command_but_has_no_reservation_or_outbox(self):
        value = draft(c.EntryMode.PAPER)
        denial = g.HardRiskPersistResult(False, None, g.HardRiskDisposition.CREATED)
        gate = self.gateway(value, risk=denial)
        result = gate.admit(object(), value)
        self.assertEqual(result.disposition, g.EntryAdmissionDisposition.RISK_REJECTED)
        self.assertEqual(len(gate._outbox.calls), 0)

    def test_increase_requires_authoritative_reservation_and_same_connection(self):
        value, connection = draft(c.EntryMode.SHADOW), object()
        gate = self.gateway(value)
        result = gate.admit(connection, value)
        self.assertEqual(result.disposition, g.EntryAdmissionDisposition.CREATED)
        self.assertIs(gate._command_graphs.calls[0][0], connection)
        self.assertIs(gate._hard_risk.calls[0][0], connection)
        self.assertIs(gate._outbox.calls[0][0], connection)
        with self.assertRaises(g.EntryAdmissionConflict): self.gateway(value, risk=allowed(reservation=False)).admit(object(), value)

    def test_reduce_close_emergency_protection_and_cancel_forbid_reservations(self):
        for action in (o.OrderAction.REDUCE, o.OrderAction.CLOSE, o.OrderAction.EMERGENCY_CLOSE, o.OrderAction.PROTECTION, o.OrderAction.CANCEL):
            with self.subTest(action=action):
                source = c.EntrySource.PROTECTION if action is o.OrderAction.PROTECTION else c.EntrySource.REST
                actor = o.Actor.PROTECTION if action is o.OrderAction.PROTECTION else o.Actor.HUMAN
                value = draft(c.EntryMode.PAPER, action, source, actor)
                self.assertEqual(value.request.risk_effect, o.RiskEffect.NEUTRAL if action is o.OrderAction.CANCEL else o.RiskEffect.REDUCE_RISK)
                gate = self.gateway(value, risk=allowed(reservation=True))
                with self.assertRaises(g.EntryAdmissionConflict): gate.admit(object(), value)
                self.assertEqual(len(gate._outbox.calls), 0)

    def test_non_increase_without_reservation_succeeds(self):
        for action in (o.OrderAction.REDUCE, o.OrderAction.CLOSE, o.OrderAction.EMERGENCY_CLOSE, o.OrderAction.PROTECTION, o.OrderAction.CANCEL):
            with self.subTest(action=action):
                source = c.EntrySource.PROTECTION if action is o.OrderAction.PROTECTION else c.EntrySource.REST
                actor = o.Actor.PROTECTION if action is o.OrderAction.PROTECTION else o.Actor.HUMAN
                value = draft(c.EntryMode.PAPER, action, source, actor)
                self.assertEqual(self.gateway(value, risk=allowed(reservation=False)).admit(object(), value).disposition, g.EntryAdmissionDisposition.CREATED)

    def test_replay_is_exact_and_mixed_results_are_created(self):
        value = draft(c.EntryMode.PAPER)
        self.assertEqual(self.gateway(value, command_replayed=True, risk=allowed(replayed=True), outbox_replayed=True).admit(object(), value).disposition, g.EntryAdmissionDisposition.REPLAYED)
        self.assertEqual(self.gateway(value, command_replayed=True, risk=allowed(replayed=True), outbox_replayed=False).admit(object(), value).disposition, g.EntryAdmissionDisposition.CREATED)

    def test_restricted_sources_default_disabled_without_ports(self):
        for source, actor in ((c.EntrySource.AGENT, o.Actor.AGENT), (c.EntrySource.MCP, o.Actor.MCP), (c.EntrySource.GRID, o.Actor.GRID)):
            with self.subTest(source=source):
                value = draft(None, o.OrderAction.OPEN, source, actor)
                gate = self.gateway(value, risk=allowed(reservation=False))
                self.assertEqual(gate.admit(object(), value).disposition, g.EntryAdmissionDisposition.DISABLED)
                self.assertEqual((len(gate._mapper.calls), len(gate._command_graphs.calls), len(gate._hard_risk.calls), len(gate._outbox.calls)), (0, 0, 0, 0))

    def test_gateway_has_no_transaction_or_runtime_escape_hatch(self):
        source = inspect.getsource(g)
        for forbidden in (".commit(", ".rollback(", "live", "exchange", "executor", "worker", "scheduler", "route", "persist_reservation"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
