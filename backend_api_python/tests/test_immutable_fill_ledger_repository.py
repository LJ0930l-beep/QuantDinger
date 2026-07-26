from __future__ import annotations

from datetime import datetime, timezone
import unittest

from tests.pr06_contract_loader import load_pr06_contracts
from tests.test_immutable_fill_ledger import fill_input


modules = load_pr06_contracts()
ledger = modules.ledger
repository = modules.repository


class FakeCursor:
    def __init__(self, responses=(), error=None):
        self.responses = list(responses)
        self.error = error
        self.executed = []
        self.closed = False

    def execute(self, statement, params=()):
        self.executed.append((" ".join(statement.split()), params))
        if self.error is not None:
            raise self.error

    def fetchone(self):
        return self.responses.pop(0) if self.responses else None

    def fetchall(self):
        value = self.responses.pop(0) if self.responses else []
        return value if isinstance(value, list) else [value]

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, responses=(), error=None):
        self.cursor_value = FakeCursor(responses, error)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeUniqueViolation(Exception):
    pgcode = "23505"


def scope():
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    return repository.FillLedgerPersistenceScope(
        tenant_id=1,
        credential_id=2,
        intent_id="00000000-0000-0000-0000-000000000002",
        economic_order_id="00000000-0000-0000-0000-000000000001",
        source="REST",
        exchange_event_at=now,
        received_at=now,
        normalizer_version="normalizer-v1",
        instrument_rule_version="rule-v1",
    )


class ImmutableFillLedgerRepositoryTests(unittest.TestCase):
    def test_persist_writes_fill_evidence_fee_and_balanced_transactions_atomically(self):
        connection = FakeConnection([("fill-event",)])
        result = repository.ImmutableFillLedgerRepository().persist_fill_bundle(
            connection, scope=scope(), fill=fill_input()
        )
        self.assertEqual(result.disposition, repository.FillLedgerCommitDisposition.APPLIED)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        sql = "\n".join(statement for statement, _ in connection.cursor_value.executed)
        self.assertIn("qd_exchange_fill_events", sql)
        self.assertIn("qd_ledger_valuation_evidence", sql)
        self.assertIn("qd_exchange_fill_fee_components", sql)
        self.assertIn("qd_ledger_transactions", sql)
        self.assertIn("qd_ledger_entries", sql)
        self.assertNotIn("UPDATE qd_ledger", sql)
        self.assertNotIn("DELETE FROM qd_ledger", sql)

    def test_exact_duplicate_returns_typed_replay_without_new_facts(self):
        bundle = ledger.reduce_fill_to_ledger_bundle(fill_input())
        fill_event_id = repository._stable_uuid(f"fill-event:{bundle.fill_key}")
        trade_id = repository._stable_uuid(f"ledger-transaction:{bundle.trade.source_fingerprint}")
        assert bundle.fee is not None
        fee_id = repository._stable_uuid(f"ledger-transaction:{bundle.fee.source_fingerprint}")
        current = fill_input()
        fill_row = (
            fill_event_id, 1, 2, current.account_scope, current.venue_fill.order_scope.market_type,
            current.economic_order_id, scope().intent_id, current.venue_fill.order_scope.exchange_order_id,
            current.venue_fill.venue_fill_id, current.venue_fill.order_scope.instrument,
            current.side.value, repository._payload_hash(bundle),
        )
        connection = FakeConnection([
            None,
            fill_row,
            [(trade_id, "TRADE", bundle.trade.source_fingerprint), (fee_id, "FEE", bundle.fee.source_fingerprint)],
        ])
        result = repository.ImmutableFillLedgerRepository().persist_fill_bundle(
            connection, scope=scope(), fill=current
        )
        self.assertEqual(result.disposition, repository.FillLedgerCommitDisposition.REPLAYED)
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        writes = [statement for statement, _ in connection.cursor_value.executed if statement.startswith("INSERT")]
        self.assertEqual(len(writes), 1)

    def test_duplicate_with_changed_immutable_facts_is_typed_conflict_and_rolls_back(self):
        bundle = ledger.reduce_fill_to_ledger_bundle(fill_input())
        fill_event_id = repository._stable_uuid(f"fill-event:{bundle.fill_key}")
        wrong_row = (
            fill_event_id, 1, 2, "other-account", "swap",
            fill_input().economic_order_id, scope().intent_id, "order-1", "venue-fill-1",
            "BTC-USDT", "BUY", repository._payload_hash(bundle),
        )
        connection = FakeConnection([None, wrong_row])
        with self.assertRaises(repository.FillLedgerReplayConflict):
            repository.ImmutableFillLedgerRepository().persist_fill_bundle(
                connection, scope=scope(), fill=fill_input()
            )
        self.assertEqual(connection.rollbacks, 1)

    def test_database_unique_errors_never_escape_raw(self):
        connection = FakeConnection(error=FakeUniqueViolation("duplicate"))
        with self.assertRaises(repository.FillLedgerPersistenceConflict):
            repository.ImmutableFillLedgerRepository().persist_fill_bundle(
                connection, scope=scope(), fill=fill_input()
            )
        self.assertEqual(connection.rollbacks, 1)

    def test_scope_requires_strict_utc_and_matching_economic_order(self):
        with self.assertRaises(ledger.ImmutableLedgerContractError):
            repository.FillLedgerPersistenceScope(
                tenant_id=1, credential_id=2,
                intent_id="00000000-0000-0000-0000-000000000002",
                economic_order_id="00000000-0000-0000-0000-000000000001",
                source="REST", exchange_event_at=datetime.now(), received_at=datetime.now(),
                normalizer_version="v1", instrument_rule_version="v1",
            )
        other_scope = repository.FillLedgerPersistenceScope(
            tenant_id=1, credential_id=2,
            intent_id="00000000-0000-0000-0000-000000000002",
            economic_order_id="00000000-0000-0000-0000-000000000099",
            source="REST", exchange_event_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            received_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
            normalizer_version="v1", instrument_rule_version="v1",
        )
        with self.assertRaises(repository.FillLedgerReplayConflict):
            repository.ImmutableFillLedgerRepository().persist_fill_bundle(
                FakeConnection(), scope=other_scope, fill=fill_input()
            )


if __name__ == "__main__":
    unittest.main()
