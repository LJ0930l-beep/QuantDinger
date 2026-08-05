from datetime import datetime, timezone
import unittest

from tests.pr12c_admission_loader import load_pr12c_admission

m = load_pr12c_admission()
r, e, v2, o = m.runtime_ingress, m.entry, m.entry_v2, m.order

def ingress(**changes):
    values = dict(credential_id=2, instrument_id="BTCUSDT", market_type="swap", action=o.OrderAction.OPEN,
                  side=e.OrderSide.BUY, quantity="1", quantity_semantics=v2.QuantitySemantics.ABSOLUTE,
                  execution_kind=e.ExecutionKind.LIMIT, limit_price="100", idempotency_key="case-1")
    values.update(changes); return r.RuntimeEntryIngressV1(**values)
def principal(source=e.EntrySource.REST): return r.RuntimeIngressPrincipal(1, "human-1", source)
def scope(): return r.AuthoritativeIngressScope(1, 2, "account-1")

class RuntimeIngressTests(unittest.TestCase):
 def test_explicit_decimal_facts_reject_float_and_implicit_amounts(self):
  with self.assertRaises(r.RuntimeEntryIngressError): ingress(quantity=1.0)
  with self.assertRaises(r.RuntimeEntryIngressError): ingress(limit_price=100.0)
  with self.assertRaises(r.RuntimeEntryIngressError): ingress(quantity=None)
 def test_stable_identity_excludes_time_and_correlation_and_cancel_has_no_economic_order(self):
  first=r.derive_durable_entry_identity(ingress(),principal=principal(),scope=scope()); second=r.derive_durable_entry_identity(ingress(),principal=principal(),scope=scope())
  self.assertEqual(first,second); self.assertTrue(first.economic_order_id)
  cancel=ingress(action=o.OrderAction.CANCEL,side=None,quantity=None,quantity_semantics=None,execution_kind=None,limit_price=None,cancel_target_kind=v2.CancelTargetKind.CLIENT_ORDER_ID,cancel_target_id="client-1")
  self.assertIsNone(r.derive_durable_entry_identity(cancel,principal=principal(),scope=scope()).economic_order_id)
 def test_scope_source_and_action_are_authoritative_and_lossless(self):
  request=r.build_runtime_entry_request(ingress(),principal=principal(),scope=scope(),correlation_id="corr-1",occurred_at=datetime(2026,7,30,tzinfo=timezone.utc))
  self.assertEqual(request.actor.entry_source,e.EntrySource.REST); self.assertEqual(request.economic_intent.quantity.to_string(),"1"); self.assertEqual(request.economic_intent.limit_price.to_string(),"100")
  with self.assertRaises(r.RuntimeEntryIngressError): r.build_runtime_entry_request(ingress(),principal=principal(),scope=r.AuthoritativeIngressScope(1,3,"account-1"),correlation_id="corr-1",occurred_at=datetime(2026,7,30,tzinfo=timezone.utc))
 def test_restricted_sources_remain_disabled(self):
  for source in (e.EntrySource.AGENT,e.EntrySource.MCP,e.EntrySource.GRID):
   request=r.build_runtime_entry_request(ingress(),principal=principal(source),scope=scope(),correlation_id="corr-1",occurred_at=datetime(2026,7,30,tzinfo=timezone.utc))
   self.assertEqual(request.mode,e.EntryMode.DISABLED)
 def test_no_live_or_runtime_dependencies(self):
  from pathlib import Path
  source=(Path(__file__).resolve().parents[1]/"app"/"domain"/"runtime_entry_ingress_contracts.py").read_text(encoding="utf-8")
  self.assertNotIn("LIVE",source); self.assertNotIn("app.services",source); self.assertNotIn("commit(",source); self.assertNotIn("rollback(",source)
