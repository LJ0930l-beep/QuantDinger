from __future__ import annotations

from unittest.mock import Mock
import uuid

import pytest

from app.domain.order_state_machine import SubmissionAttemptScope
from app.services.submission_attempt_repository import (
    SubmissionAttemptConflict,
    SubmissionAttemptCreateFacts,
    SubmissionAttemptDisposition,
    SubmissionAttemptRepository,
)


class _Cursor:
    def __init__(self, *, inserted=None, rows=()):
        self.inserted = inserted
        self.rows = list(rows)
        self.calls = []
        self.closed = False

    def execute(self, query, params=()):
        self.calls.append((query, params))

    def fetchone(self):
        value, self.inserted = self.inserted, None
        return value

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _facts() -> SubmissionAttemptCreateFacts:
    economic_order_id = str(uuid.uuid4())
    scope = SubmissionAttemptScope(1, 2, "account-a", "BTC_USDT", "spot", economic_order_id, "gate")
    return SubmissionAttemptCreateFacts(
        id=str(uuid.uuid4()),
        scope=scope,
        child_seq=1,
        attempt_no=1,
        role="PRIMARY",
        canonical_client_order_id="gate-v1-client",
        venue_client_order_id="t-client",
        request_fingerprint="request-fingerprint",
        request_json_redacted={"quantity": "1", "side": "buy"},
        venue_capability_snapshot_id=str(uuid.uuid4()),
        recovery_policy_snapshot_id=str(uuid.uuid4()),
        client_id_algorithm_version="gate-client-v1",
        broker_prefix_normalization_version="prefix-v1",
        broker_prefix="qd",
    )


def _row(facts: SubmissionAttemptCreateFacts):
    return (
        facts.id, facts.scope.economic_order_id, facts.scope.exchange, facts.scope.tenant_id,
        facts.scope.credential_id, facts.scope.account_scope, facts.scope.instrument_id,
        facts.scope.market_type, facts.child_seq, facts.attempt_no, facts.role,
        facts.canonical_client_order_id, facts.venue_client_order_id, facts.request_fingerprint,
        {"side": "buy", "quantity": "1"}, facts.venue_capability_snapshot_id,
        facts.recovery_policy_snapshot_id, facts.client_id_algorithm_version,
        facts.broker_prefix_normalization_version, facts.broker_prefix, facts.canonical_contract_version,
    )


def test_caller_owned_insert_does_not_commit_and_closes_cursor():
    facts = _facts()
    cursor = _Cursor(inserted=(facts.id,))
    connection = _Connection(cursor)

    result = SubmissionAttemptRepository().persist_caller_owned(connection, facts)

    assert result.disposition is SubmissionAttemptDisposition.APPLIED
    assert result.attempt_id == facts.id
    assert connection.commits == 0
    assert connection.rollbacks == 0
    assert cursor.closed is True


def test_exact_replay_is_typed_and_does_not_commit():
    facts = _facts()
    cursor = _Cursor(inserted=None, rows=[_row(facts)])
    connection = _Connection(cursor)

    result = SubmissionAttemptRepository().persist_caller_owned(connection, facts)

    assert result == type(result)(facts.id, SubmissionAttemptDisposition.REPLAYED)
    assert connection.commits == 0
    assert connection.rollbacks == 0


def test_immutable_mismatch_is_typed_conflict():
    facts = _facts()
    existing = list(_row(facts))
    existing[12] = "t-different"
    cursor = _Cursor(inserted=None, rows=[tuple(existing)])

    with pytest.raises(SubmissionAttemptConflict):
        SubmissionAttemptRepository().persist_caller_owned(_Connection(cursor), facts)


def test_compatibility_wrapper_owns_commit_only_on_success():
    facts = _facts()
    connection = _Connection(_Cursor(inserted=(facts.id,)))

    result = SubmissionAttemptRepository().persist(connection, facts)

    assert result.disposition is SubmissionAttemptDisposition.APPLIED
    assert connection.commits == 1
    assert connection.rollbacks == 0
