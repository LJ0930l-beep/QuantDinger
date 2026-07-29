"""Contract-lock tests for the PR-12b1 durable Canonical Entry boundary."""

from __future__ import annotations

import unittest

from tests.pr11_contract_loader import load_pr11_contracts


m = load_pr11_contracts()
d = m.durable_entry
OrderAction = m.order.OrderAction
RiskEffect = m.order.RiskEffect


class DurableEntryPersistenceContractTests(unittest.TestCase):
    def test_contract_locks_table_identity_and_full_typed_column_set(self):
        self.assertEqual("qd_durable_entry_specifications", d.DURABLE_ENTRY_SPECIFICATION_TABLE)
        self.assertEqual("canonical-entry-v2", d.DURABLE_ENTRY_CONTRACT_VERSION)
        self.assertEqual(
            ("tenant_id", "credential_id", "account_scope", "idempotency_key", "contract_version"),
            d.DURABLE_ENTRY_IDEMPOTENCY_COLUMNS,
        )
        self.assertIn("command_id", d.DURABLE_ENTRY_SQL_COLUMNS)
        self.assertIn("economic_order_id", d.DURABLE_ENTRY_SQL_COLUMNS)
        self.assertIn("trigger_direction", d.DURABLE_ENTRY_SQL_COLUMNS)
        self.assertIn("trigger_price_type", d.DURABLE_ENTRY_SQL_COLUMNS)
        self.assertIn("correlation_id", d.DURABLE_ENTRY_AUTHORITATIVE_COLUMNS)
        self.assertIn("occurred_at", d.DURABLE_ENTRY_AUTHORITATIVE_COLUMNS)
        self.assertNotIn("created_at", d.DURABLE_ENTRY_AUTHORITATIVE_COLUMNS)
        self.assertEqual("full-sql-mirror", d.DURABLE_ENTRY_INIT_MIRROR_POLICY)

    def test_action_subject_and_economic_order_mapping_is_fail_closed(self):
        for action in (OrderAction.OPEN, OrderAction.INCREASE):
            rule = d.durable_entry_action_rule(action)
            self.assertIs(rule.risk_effect, RiskEffect.INCREASE_RISK)
            self.assertIs(rule.subject_type, m.entry_v2.EconomicOrderSubject)
            self.assertTrue(rule.economic_order_required)
        for action in (OrderAction.REDUCE, OrderAction.CLOSE, OrderAction.EMERGENCY_CLOSE, OrderAction.PROTECTION):
            rule = d.durable_entry_action_rule(action)
            self.assertIs(rule.risk_effect, RiskEffect.REDUCE_RISK)
            self.assertIs(rule.subject_type, m.entry_v2.EconomicOrderSubject)
            self.assertTrue(rule.economic_order_required)
        cancel = d.durable_entry_action_rule(OrderAction.CANCEL)
        self.assertIs(cancel.risk_effect, RiskEffect.NEUTRAL)
        self.assertIs(cancel.subject_type, m.entry_v2.CancelTargetSubject)
        self.assertFalse(cancel.economic_order_required)
        with self.assertRaises(d.DurableEntryIntegrityError):
            d.durable_entry_action_rule("CANCEL")

    def test_result_and_typed_error_names_are_stable(self):
        self.assertEqual("CREATED", d.DurableEntryPersistDisposition.CREATED.value)
        self.assertEqual("REPLAYED", d.DurableEntryPersistDisposition.REPLAYED.value)
        self.assertTrue(issubclass(d.DurableEntryConflict, d.DurableEntryRepositoryError))
        self.assertTrue(issubclass(d.DurableEntryIntegrityError, d.DurableEntryRepositoryError))


if __name__ == "__main__":
    unittest.main()
