from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import uuid4

from tests.pr12_contract_loader import load_pr12_gateway

modules = load_pr12_gateway()
c, d, o, g = modules.canonical, modules.decimal, modules.order, modules.gateway


def draft(mode):
    return c.CanonicalCommandDraft(c.CanonicalEntryRequest(1, 2, "account-a", "BTC-USDT", "usdm", o.OrderAction.OPEN,
        c.CanonicalEconomicIntent(c.OrderSide.BUY, d.Quantity("1"), c.ExecutionKind.MARKET),
        c.EntryActorContext(o.Actor.HUMAN, "human:1", c.EntrySource.REST), "case-1", "corr-1", datetime.now(timezone.utc), mode=mode))


class Port:
    def __init__(self, result): self.result, self.calls = result, 0
    def map(self, _): self.calls += 1; return object()
    def persist_command_graph(self, *_): self.calls += 1; return self.result
    def persist_for_admission(self, *_): self.calls += 1; return self.result
    def persist_admission(self, *_): self.calls += 1; return self.result


class Result:
    def __init__(self, *, allowed=True, reservation_persisted=True, replayed=False):
        self.allowed, self.reservation_persisted, self.replayed = allowed, reservation_persisted, replayed
        self.command_id, self.intent_id, self.economic_order_id = str(uuid4()), str(uuid4()), str(uuid4())


class EntryAdmissionGatewayTests(unittest.TestCase):
    def test_disabled_has_zero_port_calls(self):
        mapper = Port(Result()); command = Port(Result()); risk = Port(Result()); outbox = Port(Result())
        result = g.CanonicalEntryAdmissionGateway(mapper=mapper, command_graphs=command, hard_risk=risk, outbox=outbox).admit(object(), draft(c.EntryMode.DISABLED))
        self.assertEqual(result.disposition, g.EntryAdmissionDisposition.DISABLED)
        self.assertEqual((mapper.calls, command.calls, risk.calls, outbox.calls), (0, 0, 0, 0))

    def test_paper_admits_without_transaction_control_or_exchange(self):
        mapper = Port(Result()); command = Port(Result()); risk = Port(Result()); outbox = Port(Result())
        result = g.CanonicalEntryAdmissionGateway(mapper=mapper, command_graphs=command, hard_risk=risk, outbox=outbox).admit(object(), draft(c.EntryMode.PAPER))
        self.assertEqual(result.disposition, g.EntryAdmissionDisposition.CREATED)
        self.assertEqual((mapper.calls, command.calls, risk.calls, outbox.calls), (1, 1, 1, 1))

    def test_hard_risk_rejection_never_writes_outbox(self):
        mapper = Port(Result()); command = Port(Result()); risk = Port(Result(allowed=False, reservation_persisted=False)); outbox = Port(Result())
        result = g.CanonicalEntryAdmissionGateway(mapper=mapper, command_graphs=command, hard_risk=risk, outbox=outbox).admit(object(), draft(c.EntryMode.SHADOW))
        self.assertEqual(result.disposition, g.EntryAdmissionDisposition.RISK_REJECTED)
        self.assertEqual(outbox.calls, 0)


if __name__ == "__main__": unittest.main()
