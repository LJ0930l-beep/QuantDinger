"""PostgreSQL integration coverage for PR-06 immutable fill-ledger persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import threading
import unittest
import uuid

from tests.pr06_contract_loader import load_pr06_contracts
from tests.test_immutable_fill_ledger import fill_input
from tests.test_unified_order_schema import UnifiedOrderSchemaPostgresTests


modules = load_pr06_contracts()
ledger = modules.ledger
repository = modules.repository


def _scope(graph):
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    return repository.FillLedgerPersistenceScope(
        tenant_id=graph["user_id"],
        credential_id=graph["credential_id"],
        intent_id=graph["intent_id"],
        economic_order_id=graph["economic_order_id"],
        source="REST",
        exchange_event_at=now,
        received_at=now,
        normalizer_version="normalizer-v1",
        instrument_rule_version="rule-v1",
    )


@unittest.skipUnless(os.getenv("DATABASE_URL"), "requires CI PostgreSQL DATABASE_URL")
class ImmutableFillLedgerPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg2

        self.psycopg2 = psycopg2
        self.connection = psycopg2.connect(os.environ["DATABASE_URL"])
        self.connection.autocommit = False
        with self.connection.cursor() as cursor:
            self.graph = UnifiedOrderSchemaPostgresTests()._create_order_graph(cursor)
        self.connection.commit()

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def _assert_rejected(self, statement, parameters=()):
        with self.connection.cursor() as cursor:
            cursor.execute("SAVEPOINT expected_rejection")
            try:
                with self.assertRaises(self.psycopg2.Error):
                    cursor.execute(statement, parameters)
            finally:
                cursor.execute("ROLLBACK TO SAVEPOINT expected_rejection")
                cursor.execute("RELEASE SAVEPOINT expected_rejection")

    def _persist(self, fill=None):
        return repository.ImmutableFillLedgerRepository().persist_fill_bundle(
            self.connection,
            scope=_scope(self.graph),
            fill=fill or self._fill(),
        )

    def _fill(self, **changes):
        return fill_input(
            economic_order_id=self.graph["economic_order_id"],
            market_type="spot",
            **changes,
        )

    def test_atomic_fill_fee_evidence_and_balanced_entries_commit_together(self):
        result = self._persist()
        self.assertEqual(result.disposition, repository.FillLedgerCommitDisposition.APPLIED)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM qd_exchange_fill_events WHERE id = %s", (result.fill_event_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM qd_ledger_valuation_evidence WHERE fill_event_id = %s", (result.fill_event_id,))
            self.assertEqual(cursor.fetchone()[0], 3)
            cursor.execute("SELECT count(*) FROM qd_exchange_fill_fee_components WHERE fill_event_id = %s", (result.fill_event_id,))
            self.assertEqual(cursor.fetchone()[0], 2)
            cursor.execute("SELECT count(*) FROM qd_ledger_transactions WHERE source_event_id = %s", (result.fill_event_id,))
            self.assertEqual(cursor.fetchone()[0], 2)
            cursor.execute(
                "SELECT count(*) FROM qd_ledger_entries WHERE transaction_id IN (%s, %s)",
                (result.trade_transaction_id, result.fee_transaction_id),
            )
            self.assertEqual(cursor.fetchone()[0], 12)

    def test_exact_replay_is_typed_and_changed_immutable_fact_is_typed_conflict(self):
        first = self._persist()
        replay = self._persist()
        self.assertEqual(first.disposition, repository.FillLedgerCommitDisposition.APPLIED)
        self.assertEqual(replay.disposition, repository.FillLedgerCommitDisposition.REPLAYED)
        with self.assertRaises(repository.FillLedgerReplayConflict):
            self._persist(self._fill(side=ledger.FillSide.SELL))

    def test_append_only_guards_reject_update_and_delete(self):
        result = self._persist()
        self._assert_rejected(
            "UPDATE qd_ledger_transactions SET description_code = 'mutated' WHERE id = %s",
            (result.trade_transaction_id,),
        )
        self._assert_rejected(
            "DELETE FROM qd_ledger_entries WHERE transaction_id = %s",
            (result.trade_transaction_id,),
        )
        self._assert_rejected(
            "UPDATE qd_exchange_fill_events SET side = 'SELL' WHERE id = %s",
            (result.fill_event_id,),
        )

    def test_commit_time_constraints_reject_unbalanced_quantity_and_monetary_books(self):
        for book, values in (
            ("QUANTITY", (("BTC", "1", None), ("BTC", "-0.9", None))),
            ("MONETARY", (("USDT", "1", "1"), ("USDT", "-1", "-0.9"))),
        ):
            transaction_id = str(uuid.uuid4())
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO qd_ledger_transactions (
                        id, tenant_id, credential_id, account_scope, transaction_type,
                        source_event_type, source_event_id, source_fingerprint,
                        effective_at, valuation_ccy, policy_version, description_code
                    ) VALUES (%s,%s,%s,'account-a','TRADE',%s,%s,%s,NOW(),'USDT','fill-ledger-v1','test')
                    """,
                    (transaction_id, self.graph["user_id"], self.graph["credential_id"],
                     f"PR06_UNBALANCED_{book}", str(uuid.uuid4()), uuid.uuid4().hex + uuid.uuid4().hex),
                )
                for line_no, (asset, amount, valuation) in enumerate(values, 1):
                    cursor.execute(
                        """
                        INSERT INTO qd_ledger_entries (
                            id, transaction_id, line_no, book, account_code, asset,
                            signed_amount, value_in_valuation_ccy, instrument_id, economic_order_id
                        ) VALUES (%s,%s,%s,%s,'TEST',%s,%s,%s,'BTC-USDT',%s)
                        """,
                        (str(uuid.uuid4()), transaction_id, line_no, book, asset, amount, valuation,
                         self.graph["economic_order_id"]),
                    )
            with self.assertRaises(self.psycopg2.Error):
                self.connection.commit()
            self.connection.rollback()

    def test_reversal_is_append_only_and_uses_a_distinct_source_fingerprint(self):
        original = self._persist()
        reversal_id = str(uuid.uuid4())
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO qd_ledger_transactions (
                    id, tenant_id, credential_id, account_scope, transaction_type,
                    source_event_type, source_event_id, source_fingerprint,
                    reverses_transaction_id, effective_at, valuation_ccy, policy_version, description_code
                ) VALUES (%s,%s,%s,'account-a','REVERSAL','PR06_REVERSAL',%s,%s,%s,NOW(),'USDT','fill-ledger-v1','test')
                """,
                (reversal_id, self.graph["user_id"], self.graph["credential_id"], str(uuid.uuid4()),
                 uuid.uuid4().hex + uuid.uuid4().hex, original.trade_transaction_id),
            )
            for line_no, amount in ((1, "1"), (2, "-1")):
                cursor.execute(
                    """
                    INSERT INTO qd_ledger_entries (
                        id, transaction_id, line_no, book, account_code, asset,
                        signed_amount, value_in_valuation_ccy, instrument_id, economic_order_id
                    ) VALUES (%s,%s,%s,'QUANTITY','REVERSAL','BTC',%s,NULL,'BTC-USDT',%s)
                    """,
                    (str(uuid.uuid4()), reversal_id, line_no, amount, self.graph["economic_order_id"]),
                )
        self.connection.commit()
        self._assert_rejected(
            "INSERT INTO qd_ledger_transactions "
            "(id, tenant_id, credential_id, account_scope, transaction_type, source_event_type, source_event_id, source_fingerprint, reverses_transaction_id, effective_at, valuation_ccy, policy_version, description_code) "
            "VALUES (%s,%s,%s,'account-a','REVERSAL','PR06_REVERSAL_AGAIN',%s,%s,%s,NOW(),'USDT','fill-ledger-v1','test')",
            (str(uuid.uuid4()), self.graph["user_id"], self.graph["credential_id"], str(uuid.uuid4()),
             uuid.uuid4().hex + uuid.uuid4().hex, original.trade_transaction_id),
        )

    def test_rollback_injection_leaves_no_partial_fill(self):
        database_connection = self.connection

        class FailingCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def execute(self, statement, params=()):
                if "INSERT INTO qd_ledger_valuation_evidence" in statement:
                    raise RuntimeError("injected persistence failure")
                return self.cursor.execute(statement, params)

            def fetchone(self):
                return self.cursor.fetchone()

            def fetchall(self):
                return self.cursor.fetchall()

            def close(self):
                self.cursor.close()

        class FailingConnection:
            def cursor(self):
                return FailingCursor(database_connection.cursor())

            def commit(self):
                database_connection.commit()

            def rollback(self):
                database_connection.rollback()

        fill = self._fill(account_scope="account-a")
        fill_key = fill.venue_fill.canonical_key
        with self.assertRaises(repository.ImmutableLedgerRepositoryError):
            repository.ImmutableFillLedgerRepository().persist_fill_bundle(
                FailingConnection(), scope=_scope(self.graph), fill=fill
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM qd_exchange_fill_events WHERE credential_id = %s AND dedupe_key = %s",
                (self.graph["credential_id"], fill_key),
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_two_connections_create_one_bundle_and_the_other_replays(self):
        graph = self.graph
        barrier = threading.Barrier(2)

        def submit():
            connection = self.psycopg2.connect(os.environ["DATABASE_URL"])
            try:
                connection.autocommit = False
                barrier.wait(timeout=10)
                return repository.ImmutableFillLedgerRepository().persist_fill_bundle(
                    connection,
                    scope=_scope(graph),
                    fill=fill_input(economic_order_id=graph["economic_order_id"], market_type="spot"),
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(submit) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(
            {result.disposition for result in results},
            {repository.FillLedgerCommitDisposition.APPLIED, repository.FillLedgerCommitDisposition.REPLAYED},
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM qd_exchange_fill_events WHERE credential_id = %s AND dedupe_key = %s",
                (graph["credential_id"], self._fill().venue_fill.canonical_key),
            )
            self.assertEqual(cursor.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
