from __future__ import annotations

import unittest
from uuid import uuid4

from tests.pr12c_admission_loader import load_pr12c_admission


class RuntimeEntryResolutionContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = load_pr12c_admission()
        cls.EntryMode, cls.EntrySource, cls.OrderSide, cls.PositionSide = (cls.modules.entry.EntryMode, cls.modules.entry.EntrySource, cls.modules.entry.OrderSide, cls.modules.entry.PositionSide)
        cls.ExecutionKind, cls.QuantitySemantics = cls.modules.entry_v2.ExecutionKind, cls.modules.entry_v2.QuantitySemantics
        cls.Actor, cls.OrderAction = cls.modules.order.Actor, cls.modules.order.OrderAction
        cls.RuntimeEntryIngressV1, cls.RuntimeIngressPrincipal = cls.modules.runtime_ingress.RuntimeEntryIngressV1, cls.modules.runtime_ingress.RuntimeIngressPrincipal
        cls.CredentialOwnership, cls.InstrumentAuthority, cls.PositionSubjectAuthority, cls.RuntimeEntryResolutionError = (cls.modules.runtime_resolution.CredentialOwnership, cls.modules.runtime_resolution.InstrumentAuthority, cls.modules.runtime_resolution.PositionSubjectAuthority, cls.modules.runtime_resolution.RuntimeEntryResolutionError)
        cls.resolve_runtime_entry_facts = staticmethod(cls.modules.runtime_resolution.resolve_runtime_entry_facts)
    def _ingress(self, action=None, **changes):
        action = self.OrderAction.OPEN if action is None else action
        values=dict(credential_id=7,instrument_id='BTC-USDT',market_type='swap',action=action,side=self.OrderSide.BUY,execution_kind=self.ExecutionKind.MARKET,idempotency_key='case-1',quantity='1',quantity_semantics=self.QuantitySemantics.ABSOLUTE,reduce_only=False,position_side=self.PositionSide.NET)
        values.update(changes); return self.RuntimeEntryIngressV1(**values)
    def _principal(self): return self.RuntimeIngressPrincipal(tenant_id=3, actor_id='user-3', source=self.EntrySource.MANUAL)
    def _facts(self): return self.CredentialOwnership(3,7,'account-7','binance'), self.InstrumentAuthority(3,7,'account-7','BTC-USDT','swap',str(uuid4()))
    def test_open_requires_authenticated_credential_and_exact_instrument_scope(self):
        credential,instrument=self._facts(); result=self.resolve_runtime_entry_facts(self._ingress(),self._principal(),credential,instrument); self.assertEqual(result.scope.account_scope,'account-7')
        with self.assertRaises(self.RuntimeEntryResolutionError): self.resolve_runtime_entry_facts(self._ingress(),self._principal(),self.CredentialOwnership(4,7,'account-7','binance'),instrument)
    def test_reduce_requires_persisted_exact_position_subject(self):
        credential,instrument=self._facts(); pid=str(uuid4()); ingress=self._ingress(self.OrderAction.REDUCE,quantity=None,quantity_semantics=None,close_quantity='1',reduce_only=True,position_side=self.PositionSide.LONG,target_position_id=pid)
        position=self.PositionSubjectAuthority(pid,3,7,'account-7','BTC-USDT','swap',self.PositionSide.LONG)
        self.assertEqual(self.resolve_runtime_entry_facts(ingress,self._principal(),credential,instrument,position).position,position)
        with self.assertRaises(self.RuntimeEntryResolutionError): self.resolve_runtime_entry_facts(ingress,self._principal(),credential,instrument)

if __name__ == '__main__': unittest.main()
