from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services.gate_testnet_execution_worker import GateTestnetExecutionError, GateTestnetExecutionWorker
from app.domain.entry_admission_v2_contracts import EntryAdmissionDisposition
from app.domain.order_state_machine import SubmissionAttemptScope
from app.services.submission_attempt_repository import SubmissionAttemptCreateFacts, SubmissionAttemptDisposition, SubmissionAttemptPersistenceResult, SubmissionAttemptRepository
from app.services.exchange_order_repository import ExchangeOrderRepository
from uuid import uuid4


def _attempt_facts():
    return SubmissionAttemptCreateFacts(
        id=str(uuid4()),
        scope=SubmissionAttemptScope(1, 2, "account", "BTC_USDT", "spot", str(uuid4()), "gate"),
        child_seq=1,
        attempt_no=1,
        role="PRIMARY",
        canonical_client_order_id="gate-v1-case",
        venue_client_order_id="t-case",
        request_fingerprint="request-case",
        venue_capability_snapshot_id=str(uuid4()),
        recovery_policy_snapshot_id=str(uuid4()),
        client_id_algorithm_version="gate-v1",
        broker_prefix_normalization_version="prefix-v1",
        broker_prefix="qd",
    )


def test_worker_is_disabled_before_any_client_call():
    worker = GateTestnetExecutionWorker(Mock(), enabled=False)
    with pytest.raises(GateTestnetExecutionError):
        worker.execute(object(), None, None, None)


def test_worker_rejects_untyped_admission_before_client_call():
    worker = GateTestnetExecutionWorker(Mock(), enabled=True)
    with pytest.raises(GateTestnetExecutionError):
        worker.execute(object(), object(), object(), object())


def test_replayed_admission_is_fail_closed_before_network_submission(monkeypatch):
    worker = GateTestnetExecutionWorker(Mock(), enabled=True)
    admission = Mock(disposition=EntryAdmissionDisposition.REPLAYED)
    monkeypatch.setattr(worker, "_validate", lambda *args: (_ for _ in ()).throw(GateTestnetExecutionError("only CREATED admission may submit to TestNet")))
    with pytest.raises(GateTestnetExecutionError, match="only CREATED"):
        worker.execute(object(), object(), admission, object())
    worker.client.submit.assert_not_called()


def test_enabled_worker_requires_attempt_persistence_before_network(monkeypatch):
    worker = GateTestnetExecutionWorker(Mock(), enabled=True)
    monkeypatch.setattr(worker, "_validate", lambda *args: None)
    with pytest.raises(GateTestnetExecutionError, match="durable Submission Attempt"):
        worker.execute(object(), object(), object(), object())
    worker.client.submit.assert_not_called()


class _ReplayAttemptRepository(SubmissionAttemptRepository):
    def persist_caller_owned(self, connection, facts):
        return SubmissionAttemptPersistenceResult(facts.id, SubmissionAttemptDisposition.REPLAYED)


def test_replayed_attempt_requires_query_before_network(monkeypatch):
    worker = GateTestnetExecutionWorker(Mock(), enabled=True)
    monkeypatch.setattr(worker, "_validate", lambda *args: None)
    with pytest.raises(GateTestnetExecutionError, match="query/recovery"):
        worker.execute(object(), object(), object(), object(), attempt_facts=_attempt_facts(), attempt_repository=_ReplayAttemptRepository(), exchange_order_repository=ExchangeOrderRepository())
    worker.client.submit.assert_not_called()
