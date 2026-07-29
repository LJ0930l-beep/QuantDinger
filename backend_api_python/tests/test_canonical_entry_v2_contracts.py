from datetime import datetime, timezone
import unittest
from uuid import uuid4
from tests.pr11_contract_loader import load_pr11_contracts
m=load_pr11_contracts(); Price,Quantity=m.decimals.Price,m.decimals.Quantity; Actor,OrderAction,RiskEffect=m.order.Actor,m.order.OrderAction,m.order.RiskEffect
EntryActorContext,EntrySource,ExecutionKind,OrderSide,PositionSide=m.entry.EntryActorContext,m.entry.EntrySource,m.entry.ExecutionKind,m.entry.OrderSide,m.entry.PositionSide
CanonicalEntryV2Error,TriggerDirection,TriggerPriceType,QuantitySemantics,CancelTargetKind,CanonicalEconomicIntentV2,CanonicalEntryRequestV2,EconomicOrderSubject,CancelTargetSubject,DurableEntryGraphV2,convert_v1_non_stop,convert_v1_cancel=(m.entry_v2.CanonicalEntryV2Error,m.entry_v2.TriggerDirection,m.entry_v2.TriggerPriceType,m.entry_v2.QuantitySemantics,m.entry_v2.CancelTargetKind,m.entry_v2.CanonicalEconomicIntentV2,m.entry_v2.CanonicalEntryRequestV2,m.entry_v2.EconomicOrderSubject,m.entry_v2.CancelTargetSubject,m.entry_v2.DurableEntryGraphV2,m.entry_v2.convert_v1_non_stop,m.entry_v2.convert_v1_cancel)

def intent(kind=ExecutionKind.MARKET, *, close=False, all_=False, cancel=False, **change):
    if cancel: base=dict(cancel_target_kind=CancelTargetKind.CLIENT_ORDER_ID,cancel_target_id='order-1')
    elif close: base=dict(side=OrderSide.SELL,execution_kind=kind,reduce_only=True,target_position_id='position-1',close_all=all_,close_quantity=None if all_ else Quantity('1'))
    else: base=dict(side=OrderSide.BUY,quantity=Quantity('1'),quantity_semantics=QuantitySemantics.ABSOLUTE,execution_kind=kind)
    if kind in (ExecutionKind.LIMIT,ExecutionKind.STOP_LIMIT): base['limit_price']=Price('100')
    if kind in (ExecutionKind.STOP_MARKET,ExecutionKind.STOP_LIMIT): base.update(trigger_price=Price('99'),trigger_direction=TriggerDirection.AT_OR_BELOW,trigger_price_type=TriggerPriceType.MARK)
    base.update(change); return CanonicalEconomicIntentV2(**base)
def request(action=OrderAction.OPEN, x=None, *, actor=None, correlation='corr-1', occurred=None, mode=None, key='case-1'):
    actor=actor or (EntryActorContext(Actor.PROTECTION,'protect',EntrySource.PROTECTION) if action is OrderAction.PROTECTION else EntryActorContext(Actor.HUMAN,'human',EntrySource.REST))
    effect=RiskEffect.NEUTRAL if action is OrderAction.CANCEL else RiskEffect.INCREASE_RISK if action in (OrderAction.OPEN,OrderAction.INCREASE) else RiskEffect.REDUCE_RISK
    return CanonicalEntryRequestV2(1,2,'account','BTCUSDT','swap',action,x or intent(close=effect is RiskEffect.REDUCE_RISK),actor,effect,key,correlation,occurred or datetime(2026,1,1,tzinfo=timezone.utc),mode)
def v1_request(action=OrderAction.OPEN, x=None):
    effect=RiskEffect.NEUTRAL if action is OrderAction.CANCEL else RiskEffect.INCREASE_RISK if action in (OrderAction.OPEN,OrderAction.INCREASE) else RiskEffect.REDUCE_RISK
    return m.entry.CanonicalEntryRequest(1,2,'account','BTCUSDT','swap',action,x or m.entry.CanonicalEconomicIntent(side=m.entry.OrderSide.BUY,quantity=Quantity('1'),execution_kind=m.entry.ExecutionKind.MARKET),EntryActorContext(Actor.HUMAN,'human',EntrySource.REST),'case-1','corr-1',datetime(2026,1,1,tzinfo=timezone.utc),effect)
class V2Tests(unittest.TestCase):
 def test_execution_matrix(self):
  for a,close in ((OrderAction.OPEN,False),(OrderAction.INCREASE,False),(OrderAction.REDUCE,True),(OrderAction.CLOSE,True)):
   for k in ExecutionKind: request(a,intent(k,close=close))
 def test_close_all_cancel_and_subjects(self):
  x=request(OrderAction.CLOSE,intent(close=True,all_=True)); self.assertIsNone(x.economic_intent.quantity); self.assertIsNone(x.economic_intent.close_quantity)
  cancel=request(OrderAction.CANCEL,intent(cancel=True)); DurableEntryGraphV2(uuid4(),cancel,CancelTargetSubject(CancelTargetKind.CLIENT_ORDER_ID,'order-1'))
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
 def test_complete_action_execution_matrix(self):
  for action in (OrderAction.OPEN,OrderAction.INCREASE):
   for kind in ExecutionKind: self.assertIsInstance(request(action,intent(kind)),CanonicalEntryRequestV2)
  for action in (OrderAction.REDUCE,OrderAction.CLOSE,OrderAction.EMERGENCY_CLOSE,OrderAction.PROTECTION):
   for all_ in (False,True):
    for kind in ExecutionKind: self.assertIsInstance(request(action,intent(kind,close=True,all_=all_)),CanonicalEntryRequestV2)
  for kind in CancelTargetKind: self.assertEqual(request(OrderAction.CANCEL,intent(cancel=True,cancel_target_kind=kind,cancel_target_id=str(uuid4()) if kind is CancelTargetKind.ECONOMIC_ORDER_ID else 'client-1')).economic_intent.cancel_target_kind,kind)
 def test_subject_and_typed_error_matrix(self):
  with self.assertRaises(CanonicalEntryV2Error): CancelTargetSubject('CLIENT_ORDER_ID','x')
  with self.assertRaises(CanonicalEntryV2Error): CancelTargetSubject(CancelTargetKind.ECONOMIC_ORDER_ID,'not-a-uuid')
  value=uuid4(); self.assertEqual(CancelTargetSubject(CancelTargetKind.ECONOMIC_ORDER_ID,value).cancel_target_id,CancelTargetSubject(CancelTargetKind.ECONOMIC_ORDER_ID,str(value).upper()).cancel_target_id)
  self.assertEqual(CancelTargetSubject(CancelTargetKind.CLIENT_ORDER_ID,'client-1').cancel_target_id,'client-1'); self.assertEqual(CancelTargetSubject(CancelTargetKind.VENUE_ORDER_ID,'venue-1').cancel_target_id,'venue-1')
  cancel=request(OrderAction.CANCEL,intent(cancel=True));
  for subject in (CancelTargetSubject(CancelTargetKind.VENUE_ORDER_ID,'order-1'),CancelTargetSubject(CancelTargetKind.CLIENT_ORDER_ID,'other')):
   with self.assertRaises(CanonicalEntryV2Error): DurableEntryGraphV2(uuid4(),cancel,subject)
  for bad in (None,'subject'):
   with self.assertRaises(CanonicalEntryV2Error): DurableEntryGraphV2(uuid4(),bad,EconomicOrderSubject(uuid4()))
  for bad in (None,'subject'):
   with self.assertRaises(CanonicalEntryV2Error): DurableEntryGraphV2(uuid4(),request(),bad)
  with self.assertRaises(CanonicalEntryV2Error): DurableEntryGraphV2(uuid4(),request(),CancelTargetSubject(CancelTargetKind.CLIENT_ORDER_ID,'client-1'))
  with self.assertRaises(CanonicalEntryV2Error): DurableEntryGraphV2('bad',request(),EconomicOrderSubject(uuid4()))
  with self.assertRaises(CanonicalEntryV2Error): EconomicOrderSubject('bad')
  for field in ('tenant_id','credential_id'):
   for value in (True,'1',None,0,-1):
    args=[1,2,'account','BTCUSDT','swap',OrderAction.OPEN,intent(),EntryActorContext(Actor.HUMAN,'human',EntrySource.REST),RiskEffect.INCREASE_RISK,'case','corr',datetime(2026,1,1,tzinfo=timezone.utc)]; args[0 if field=='tenant_id' else 1]=value
    with self.assertRaises(CanonicalEntryV2Error): CanonicalEntryRequestV2(*args)
 def test_v1_conversion_boundaries(self):
  v1=v1_request()
  self.assertEqual(convert_v1_non_stop(v1).economic_intent.execution_kind,ExecutionKind.MARKET)
  cancel=v1_request(OrderAction.CANCEL,m.entry.CanonicalEconomicIntent(cancel_target_id='order-1'))
  with self.assertRaises(CanonicalEntryV2Error): convert_v1_non_stop(cancel)
  for kind in CancelTargetKind:
   target=str(uuid4()) if kind is CancelTargetKind.ECONOMIC_ORDER_ID else 'order-1'
   if kind is CancelTargetKind.ECONOMIC_ORDER_ID: cancel=v1_request(OrderAction.CANCEL,m.entry.CanonicalEconomicIntent(cancel_target_id=target))
   self.assertEqual(convert_v1_cancel(cancel,cancel_target_kind=kind).economic_intent.cancel_target_kind,kind)
 def test_economic_fingerprint_single_fact_variants(self):
  def changed(left,right): self.assertNotEqual(left.economic_fingerprint,right.economic_fingerprint)
  open_market=request(); changed(open_market,request(x=intent(quantity=Quantity('2')))); changed(open_market,request(x=intent(side=OrderSide.SELL))); changed(open_market,request(x=intent(position_side=PositionSide.LONG)))
  open_limit=request(x=intent(ExecutionKind.LIMIT)); changed(open_limit,request(x=intent(ExecutionKind.LIMIT,limit_price=Price('101')))); changed(open_market,open_limit)
  stop=request(x=intent(ExecutionKind.STOP_MARKET)); changed(stop,request(x=intent(ExecutionKind.STOP_MARKET,trigger_price=Price('98')))); changed(stop,request(x=intent(ExecutionKind.STOP_MARKET,trigger_direction=TriggerDirection.AT_OR_ABOVE))); changed(stop,request(x=intent(ExecutionKind.STOP_MARKET,trigger_price_type=TriggerPriceType.LAST)))
  partial=request(OrderAction.CLOSE,intent(close=True)); changed(partial,request(OrderAction.CLOSE,intent(close=True,close_quantity=Quantity('2')))); changed(partial,request(OrderAction.CLOSE,intent(close=True,target_position_id='position-2'))); changed(partial,request(OrderAction.CLOSE,intent(close=True,all_=True)))
  target=str(uuid4()); cancel=request(OrderAction.CANCEL,intent(cancel=True,cancel_target_id=target)); changed(cancel,request(OrderAction.CANCEL,intent(cancel=True,cancel_target_kind=CancelTargetKind.VENUE_ORDER_ID,cancel_target_id=target))); changed(cancel,request(OrderAction.CANCEL,intent(cancel=True,cancel_target_id='order-2')))
  with self.assertRaises(CanonicalEntryV2Error): intent(quantity_semantics='ABSOLUTE')
  with self.assertRaises(CanonicalEntryV2Error): request(x=intent(reduce_only=True))
 def test_audit_fingerprint_single_fact_isolation(self):
  base=request()
  variants=(request(actor=EntryActorContext(Actor.HUMAN,'human-2',EntrySource.REST)),request(actor=EntryActorContext(Actor.HUMAN,'human',EntrySource.MANUAL)),request(mode=m.entry.EntryMode.SHADOW),request(key='case-2'),request(correlation='corr-2'),request(occurred=datetime(2026,1,2,tzinfo=timezone.utc)))
  for variant in variants:
   self.assertEqual(base.economic_fingerprint,variant.economic_fingerprint); self.assertNotEqual(base.request_fingerprint,variant.request_fingerprint)
 def test_v1_extended_conversion_and_fingerprint_golden(self):
  market=v1_request(); self.assertEqual(market.economic_fingerprint,'09ea833c39efe7b31696bbc56b690ca3f1394e6485776eb79d8407ac53daa649'); self.assertEqual(market.request_fingerprint,'b64dc957882eded431b91d05812817e2ae33f6bb482e733ae8b09ca6308a65e6')
  limit=v1_request(x=m.entry.CanonicalEconomicIntent(side=m.entry.OrderSide.BUY,quantity=Quantity('1'),execution_kind=m.entry.ExecutionKind.LIMIT,limit_price=Price('100'))); self.assertEqual(convert_v1_non_stop(limit).economic_intent.limit_price,Price('100'))
  reduce=v1_request(OrderAction.REDUCE,m.entry.CanonicalEconomicIntent(side=m.entry.OrderSide.SELL,execution_kind=m.entry.ExecutionKind.MARKET,reduce_only=True,target_position_id='position-1',close_quantity=Quantity('1'))); converted_reduce=convert_v1_non_stop(reduce); self.assertEqual(converted_reduce.economic_intent.target_position_id,'position-1'); self.assertEqual(converted_reduce.economic_intent.close_quantity,Quantity('1'))
  close_all=v1_request(OrderAction.CLOSE,m.entry.CanonicalEconomicIntent(side=m.entry.OrderSide.SELL,execution_kind=m.entry.ExecutionKind.MARKET,reduce_only=True,target_position_id='position-1',close_all=True)); self.assertTrue(convert_v1_non_stop(close_all).economic_intent.close_all)
  for kind in (ExecutionKind.STOP_MARKET,ExecutionKind.STOP_LIMIT):
   stop=v1_request(x=m.entry.CanonicalEconomicIntent(side=m.entry.OrderSide.BUY,quantity=Quantity('1'),execution_kind=kind,limit_price=Price('100') if kind is ExecutionKind.STOP_LIMIT else None,trigger_price=Price('99')))
   with self.assertRaises(CanonicalEntryV2Error): convert_v1_non_stop(stop)
  with self.assertRaises(CanonicalEntryV2Error): convert_v1_non_stop(object())
if __name__=='__main__': unittest.main()
