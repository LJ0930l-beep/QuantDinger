from datetime import datetime, timezone
import unittest
from uuid import uuid4
from tests.pr11_contract_loader import load_pr11_contracts
m=load_pr11_contracts(); Price,Quantity=m.decimals.Price,m.decimals.Quantity; Actor,OrderAction,RiskEffect=m.order.Actor,m.order.OrderAction,m.order.RiskEffect
EntryActorContext,EntrySource,ExecutionKind,OrderSide,PositionSide=m.entry.EntryActorContext,m.entry.EntrySource,m.entry.ExecutionKind,m.entry.OrderSide,m.entry.PositionSide
CanonicalEntryV2Error,TriggerDirection,TriggerPriceType,QuantitySemantics,CancelTargetKind,CanonicalEconomicIntentV2,CanonicalEntryRequestV2,EconomicOrderSubject,CancelTargetSubject,DurableEntryGraphV2=(m.entry_v2.CanonicalEntryV2Error,m.entry_v2.TriggerDirection,m.entry_v2.TriggerPriceType,m.entry_v2.QuantitySemantics,m.entry_v2.CancelTargetKind,m.entry_v2.CanonicalEconomicIntentV2,m.entry_v2.CanonicalEntryRequestV2,m.entry_v2.EconomicOrderSubject,m.entry_v2.CancelTargetSubject,m.entry_v2.DurableEntryGraphV2)

def intent(kind=ExecutionKind.MARKET, *, close=False, all_=False, cancel=False, **change):
    if cancel: base=dict(cancel_target_kind=CancelTargetKind.ECONOMIC_ORDER_ID,cancel_target_id='order-1')
    elif close: base=dict(side=OrderSide.SELL,execution_kind=kind,reduce_only=True,target_position_id='position-1',close_all=all_,close_quantity=None if all_ else Quantity('1'))
    else: base=dict(side=OrderSide.BUY,quantity=Quantity('1'),quantity_semantics=QuantitySemantics.ABSOLUTE,execution_kind=kind)
    if kind in (ExecutionKind.LIMIT,ExecutionKind.STOP_LIMIT): base['limit_price']=Price('100')
    if kind in (ExecutionKind.STOP_MARKET,ExecutionKind.STOP_LIMIT): base.update(trigger_price=Price('99'),trigger_direction=TriggerDirection.AT_OR_BELOW,trigger_price_type=TriggerPriceType.MARK)
    base.update(change); return CanonicalEconomicIntentV2(**base)
def request(action=OrderAction.OPEN, x=None, *, actor=None, correlation='corr-1', occurred=None):
    actor=actor or (EntryActorContext(Actor.PROTECTION,'protect',EntrySource.PROTECTION) if action is OrderAction.PROTECTION else EntryActorContext(Actor.HUMAN,'human',EntrySource.REST))
    effect=RiskEffect.NEUTRAL if action is OrderAction.CANCEL else RiskEffect.INCREASE_RISK if action in (OrderAction.OPEN,OrderAction.INCREASE) else RiskEffect.REDUCE_RISK
    return CanonicalEntryRequestV2(1,2,'account','BTCUSDT','swap',action,x or intent(close=effect is RiskEffect.REDUCE_RISK),actor,effect,'case-1',correlation,occurred or datetime(2026,1,1,tzinfo=timezone.utc))
class V2Tests(unittest.TestCase):
 def test_execution_matrix(self):
  for a,close in ((OrderAction.OPEN,False),(OrderAction.INCREASE,False),(OrderAction.REDUCE,True),(OrderAction.CLOSE,True)):
   for k in ExecutionKind: request(a,intent(k,close=close))
 def test_close_all_cancel_and_subjects(self):
  x=request(OrderAction.CLOSE,intent(close=True,all_=True)); self.assertIsNone(x.economic_intent.quantity); self.assertIsNone(x.economic_intent.close_quantity)
  cancel=request(OrderAction.CANCEL,intent(cancel=True)); DurableEntryGraphV2(uuid4(),cancel,CancelTargetSubject(CancelTargetKind.ECONOMIC_ORDER_ID,'order-1'))
  with self.assertRaises(CanonicalEntryV2Error): DurableEntryGraphV2(uuid4(),cancel,EconomicOrderSubject('x'))
 def test_every_trigger_and_target_change_changes_identity(self):
  base=request(); variants=[request(x=intent(trigger_price=Price('99'),execution_kind=ExecutionKind.STOP_MARKET,trigger_direction=TriggerDirection.AT_OR_ABOVE,trigger_price_type=TriggerPriceType.LAST)),request(OrderAction.CANCEL,intent(cancel=True,cancel_target_id='order-2')),request(OrderAction.CLOSE,intent(close=True,target_position_id='position-2')),request(OrderAction.CLOSE,intent(close=True,all_=True))]
  self.assertTrue(all(base.economic_fingerprint != v.economic_fingerprint for v in variants)); self.assertNotEqual(variants[2].economic_fingerprint,variants[3].economic_fingerprint)
 def test_invalid_stop_and_cancel_fail_closed(self):
  with self.assertRaises(CanonicalEntryV2Error): request(x=intent(ExecutionKind.STOP_MARKET,trigger_direction=None))
  with self.assertRaises(CanonicalEntryV2Error): request(OrderAction.CANCEL,intent(cancel=True,execution_kind=ExecutionKind.MARKET))
 def test_audit_is_not_economic_identity_and_protection_is_fail_closed(self):
  base=request(); changed=request(correlation='corr-2',occurred=datetime(2026,1,2,tzinfo=timezone.utc)); self.assertEqual(base.economic_fingerprint,changed.economic_fingerprint); self.assertNotEqual(base.request_fingerprint,changed.request_fingerprint)
  protection=EntryActorContext(Actor.PROTECTION,'protect',EntrySource.PROTECTION)
  for action in (OrderAction.OPEN,OrderAction.INCREASE,OrderAction.CANCEL):
   with self.assertRaises(CanonicalEntryV2Error): request(action, intent(cancel=action is OrderAction.CANCEL),actor=protection)
if __name__=='__main__': unittest.main()
