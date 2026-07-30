from __future__ import annotations

from datetime import datetime, timezone
import unittest
from uuid import uuid4

from tests.pr12c_admission_loader import load_pr12c_admission


class _Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _AuthorityPort:
    def __init__(self, authority, persist_result):
        self.authority = authority
        self.persist_result = persist_result
        self.resolve_calls = []
        self.persist_calls = []

    def resolve(self, connection, ingress, principal):
        self.resolve_calls.append((connection, ingress, principal))
        return self.authority

    def persist_ingress(self, connection, graph, authority):
        self.persist_calls.append((connection, graph, authority))
        return self.persist_result(graph, authority)


class _AdmissionPort:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def admit(self, connection, graph):
        self.calls.append((connection, graph))
        return self.result(graph)


class RuntimeEntryAdmissionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_pr12c_admission()
        cls.OrderAction = cls.modules.order.OrderAction
        cls.OrderSide = cls.modules.entry.OrderSide
        cls.PositionSide = cls.modules.entry.PositionSide
        cls.EntrySource = cls.modules.entry.EntrySource
        cls.EntryMode = cls.modules.entry.EntryMode
        cls.ExecutionKind = cls.modules.entry_v2.ExecutionKind
        cls.QuantitySemantics = cls.modules.entry_v2.QuantitySemantics
        cls.RuntimeEntryIngressV1 = cls.modules.runtime_ingress.RuntimeEntryIngressV1
        cls.RuntimeIngressPrincipal = cls.modules.runtime_ingress.RuntimeIngressPrincipal
        cls.CredentialOwnership = cls.modules.runtime_resolution.CredentialOwnership
        cls.InstrumentAuthority = cls.modules.runtime_resolution.InstrumentAuthority
        cls.resolve_facts = staticmethod(cls.modules.runtime_resolution.resolve_runtime_entry_facts)
        cls.AuthorityReferences = cls.modules.runtime_authority.RuntimeEntryAuthorityReferences
        cls.ResolvedAuthority = cls.modules.runtime_authority.ResolvedRuntimeEntryAuthority
        cls.IngressResult = cls.modules.runtime_authority.RuntimeEntryIngressPersistResult
        cls.IngressDisposition = cls.modules.runtime_authority.RuntimeEntryIngressPersistDisposition
        cls.AdmissionResult = cls.modules.admission.EntryAdmissionResultV2
        cls.AdmissionDisposition = cls.modules.admission.EntryAdmissionDisposition
        cls.Service = cls.modules.runtime_admission_service.RuntimeEntryAdmissionService
        cls.RuntimeDisposition = cls.modules.runtime_admission.RuntimeEntryAdmissionDisposition

    def _ingress(self):
        return self.RuntimeEntryIngressV1(
            credential_id=7, instrument_id="BTC-USDT", market_type="swap",
            action=self.OrderAction.OPEN, side=self.OrderSide.BUY, quantity="1",
            quantity_semantics=self.QuantitySemantics.ABSOLUTE,
            execution_kind=self.ExecutionKind.MARKET, idempotency_key="case-1",
        )

    def _principal(self):
        return self.RuntimeIngressPrincipal(tenant_id=3, actor_id="user-3", source=self.EntrySource.MANUAL)

    def _authority(self):
        credential = self.CredentialOwnership(3, 7, "account-7", "binance")
        instrument = self.InstrumentAuthority(3, 7, "account-7", "BTC-USDT", "swap", str(uuid4()))
        facts = self.resolve_facts(self._ingress(), self._principal(), credential, instrument)
        return self.ResolvedAuthority(
            facts=facts,
            references=self.AuthorityReferences(str(uuid4()), str(uuid4())),
        )

    def _admission_result(self, disposition):
        def make(graph):
            spec = graph.specification
            return self.AdmissionResult(
                disposition=disposition, mode=spec.mode, command_id=graph.command_id,
                action=spec.action, subject=graph.subject,
                economic_order_id=graph.subject.economic_order_id,
                economic_fingerprint=spec.economic_fingerprint,
                request_fingerprint=spec.request_fingerprint,
            )
        return make

    def test_disabled_short_circuits_before_authority_admission_or_persistence(self):
        authority = _AuthorityPort(self._authority(), lambda *_: self.fail("must not persist"))
        admission = _AdmissionPort(lambda _: self.fail("must not admit"))
        connection = _Connection()
        result = self.Service(authorities=authority, admissions=admission).admit(
            connection, self._ingress(), self._principal(), correlation_id="correlation-1",
            occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc), mode=self.EntryMode.DISABLED,
        )
        self.assertEqual(result.disposition, self.RuntimeDisposition.DISABLED)
        self.assertEqual((authority.resolve_calls, authority.persist_calls, admission.calls), ([], [], []))
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))

    def test_paper_composes_one_connection_and_created_ingress_makes_result_created(self):
        authority_value = self._authority()
        authority = _AuthorityPort(
            authority_value,
            lambda graph, resolved: self.IngressResult(graph.command_id, self.IngressDisposition.CREATED, resolved),
        )
        admission = _AdmissionPort(self._admission_result(self.AdmissionDisposition.REPLAYED))
        connection = _Connection()
        result = self.Service(authorities=authority, admissions=admission).admit(
            connection, self._ingress(), self._principal(), correlation_id="correlation-1",
            occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc), mode=self.EntryMode.PAPER,
        )
        self.assertEqual(result.disposition, self.RuntimeDisposition.CREATED)
        self.assertIs(authority.resolve_calls[0][0], connection)
        self.assertIs(admission.calls[0][0], connection)
        self.assertIs(authority.persist_calls[0][0], connection)
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))


if __name__ == "__main__":
    unittest.main()
