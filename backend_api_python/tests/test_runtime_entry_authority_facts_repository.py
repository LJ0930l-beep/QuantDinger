"""Caller-owned contract coverage for Runtime Entry authority facts repository."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from app.services.runtime_entry_authority_facts_repository import (
    RuntimeEntryAuthorityFactsConflict,
    RuntimeEntryAuthorityFactsDisposition,
    RuntimeEntryAuthorityFactsRepository,
)


class _Cursor:
    def __init__(self, fetchone_result=None):
        self.queries = []
        self.params = []
        self._fetchone_result = fetchone_result

    def execute(self, query, params=()):
        self.queries.append(query)
        self.params.append(tuple(params))
        return self

    def fetchone(self):
        return self._fetchone_result

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _scope_facts(tenant_id=3, credential_id=3896):
    observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "contract_version": "runtime-entry-authority-v1",
        "tenant_id": tenant_id,
        "credential_id": credential_id,
        "account_scope": "account-1",
        "exchange_id": "gate",
        "source_identity": "gate-private-read-v1",
        "source_version": "gate-read-snapshot-v1",
        "source_fingerprint": "f" * 64,
        "observed_at": observed,
    }


class RuntimeEntryAuthorityFactsRepositoryTests(unittest.TestCase):
    def test_persist_scope_binding_never_owns_transaction(self):
        repo = RuntimeEntryAuthorityFactsRepository()
        conn = _Connection(_Cursor(fetchone_result=("11111111-1111-4111-8111-111111111111",)))
        result = repo.persist_scope_binding(conn, _scope_facts())
        self.assertEqual(result.disposition, RuntimeEntryAuthorityFactsDisposition.CREATED)
        insert_query = conn._cursor.queries[0]
        self.assertIn("INSERT INTO qd_runtime_entry_scope_bindings", insert_query)
        self.assertIn("ON CONFLICT (tenant_id, credential_id) DO NOTHING", insert_query)
        self.assertIn("RETURNING id", insert_query)
        # caller owns transaction: no commit/rollback calls exist on repo
        self.assertEqual(len(conn._cursor.queries), 1)

    def test_persist_scope_binding_replays_exact_row(self):
        repo = RuntimeEntryAuthorityFactsRepository()
        facts = _scope_facts()
        cursor = _Cursor(fetchone_result=None)  # INSERT conflict -> None
        conn = _Connection(cursor)
        # First call returns None (conflict), second SELECT returns exact row.
        # replay_columns order: tenant_id, credential_id, account_scope, exchange_id,
        # source_identity, source_version, source_fingerprint, contract_version
        def fetchone():
            if len(cursor.queries) <= 1:
                return None
            return (
                3, 3896, "account-1", "gate", "gate-private-read-v1", "gate-read-snapshot-v1",
                "f" * 64, "runtime-entry-authority-v1",
            )
        cursor.fetchone = fetchone
        result = repo.persist_scope_binding(conn, facts)
        self.assertEqual(result.disposition, RuntimeEntryAuthorityFactsDisposition.REPLAYED)
        self.assertEqual(result.id, "11111111-1111-4111-8111-111111111111")
        self.assertEqual(len(cursor.queries), 2)

    def test_exact_replay_conflict_raises(self):
        repo = RuntimeEntryAuthorityFactsRepository()
        facts = _scope_facts()
        facts["account_scope"] = "account-1"
        cursor = _Cursor(fetchone_result=None)
        conn = _Connection(cursor)

        def fetchone():
            if len(cursor.queries) <= 1:
                return None
            # wrong account_scope -> conflict
            return (
                3, 3896, "account-OTHER", "gate", "gate-private-read-v1", "gate-read-snapshot-v1",
                "f" * 64, "runtime-entry-authority-v1",
            )
        cursor.fetchone = fetchone
        with self.assertRaises(RuntimeEntryAuthorityFactsConflict):
            repo.persist_scope_binding(conn, facts)

    def test_rule_snapshot_conflict_clause_uses_rule_identity(self):
        repo = RuntimeEntryAuthorityFactsRepository()
        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        facts = {
            "id": "22222222-2222-4222-8222-222222222222",
            "exchange": "gate",
            "market_type": "spot",
            "instrument_id": "BTC_USDT",
            "rule_version": "gate-private-read-instrument-v1",
            "tick_size": Decimal("0.1"),
            "quantity_step": Decimal("0.000001"),
            "minimum_quantity": Decimal("0.00001"),
            "minimum_notional": Decimal("3"),
            "price_scale": 1,
            "quantity_scale": 6,
            "rounding_policy_version": "gate-private-read-v1",
            "raw_rules_json": {},
            "created_at": observed,
        }
        cursor = _Cursor(fetchone_result=("22222222-2222-4222-8222-222222222222",))
        result = repo.persist_instrument_rule_snapshot(_Connection(cursor), facts)
        self.assertEqual(result.disposition, RuntimeEntryAuthorityFactsDisposition.CREATED)
        self.assertIn("ON CONFLICT (exchange, market_type, instrument_id, rule_version) DO NOTHING", cursor.queries[0])


if __name__ == "__main__":
    unittest.main()
