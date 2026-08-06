from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from app.domain.runtime_entry_admission_contracts import RuntimeEntryAdmissionDisposition
from app.domain.canonical_entry_contracts import OrderSide, PositionSide
from app.services.paper_execution_repository import PaperExecutionDisposition, PaperExecutionResult
from app.services.strategy_v2.live_execution import LiveOrderRequest
from app.services.strategy_v2.paper_execution import (
    StrategyV2PaperExecutionError,
    StrategyV2PaperExecutionService,
    _signal_facts,
)
from app.domain.order_contracts import OrderAction
from uuid import uuid5, NAMESPACE_URL


NOW = int(datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc).timestamp())
ORDER_ID = "11111111-1111-4111-8111-111111111111"


class _Repository:
    def __init__(self):
        self.orders = []
        self.events = []

    def persist_order(self, connection, order):
        self.orders.append(order)
        return PaperExecutionResult(order.order_id, order.fingerprint, PaperExecutionDisposition.CREATED)

    def append_order_event(self, connection, event, *, user_id=None):
        self.events.append((event, user_id))
        return PaperExecutionDisposition.CREATED


class _Admission:
    def __init__(self, *, disposition=RuntimeEntryAdmissionDisposition.CREATED, scope="paper-scope"):
        self.disposition = disposition
        self.admission = None if disposition is RuntimeEntryAdmissionDisposition.RISK_REJECTED else SimpleNamespace(
            economic_order_id=ORDER_ID,
            request_fingerprint="request-fingerprint",
        )
        self.scope = scope
        self.calls = []

    def admit_with_graph(self, connection, ingress, principal, *, correlation_id, occurred_at, mode):
        self.calls.append((connection, ingress, principal, correlation_id, occurred_at, mode))
        return self, SimpleNamespace(specification=SimpleNamespace(account_scope=self.scope))


class _PositionCursor:
    def __init__(self, *, subject_rows=None):
        self.queries = []
        self.subject_rows = list(subject_rows or [])
        self.closed = False

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def fetchone(self):
        # The first query validates the legacy strategy-position hint.
        return (123, 12, 7, 9, "BTC_USDT", "perpetual", "long")

    def fetchall(self):
        return list(self.subject_rows)

    def close(self):
        self.closed = True


def _request(**changes):
    values = {
        "strategy_id": 7,
        "strategy_run_id": 42,
        "user_id": 12,
        "symbol": "BTC_USDT",
        "action": "open_long",
        "quantity": "0.01",
        "reference_price": "100",
        "signal_timestamp": NOW,
        "market_type": "swap",
        "execution_mode": "paper",
        "order_type": "market",
        "exchange_id": "gate",
    }
    values.update(changes)
    return LiveOrderRequest(**values)


def test_signal_facts_cover_open_increase_and_reduce_without_legacy_queue():
    assert _signal_facts("open_long")[:2] == (OrderAction.OPEN, OrderSide.BUY)
    assert _signal_facts("add_short")[0] is OrderAction.INCREASE
    assert _signal_facts("close_long")[0] is OrderAction.CLOSE
    assert _signal_facts("reduce_short")[3] is True


def test_paper_bridge_admits_then_persists_order_and_event_without_transaction_control():
    admission = _Admission()
    repository = _Repository()
    service = StrategyV2PaperExecutionService(admission_service=admission, repository=repository)
    connection = SimpleNamespace(commit=lambda: pytest.fail("bridge must not commit"), rollback=lambda: pytest.fail("bridge must not rollback"))

    receipt = service.persist(connection, _request(), credential_id=9, expected_account_scope="paper-scope")

    assert receipt.disposition is RuntimeEntryAdmissionDisposition.CREATED
    assert receipt.paper_order is not None
    assert receipt.paper_order.order_id == ORDER_ID
    assert receipt.paper_order.market_type == "perpetual"
    assert receipt.paper_order.side == "BUY"
    assert len(repository.orders) == 1
    assert len(repository.events) == 1
    assert repository.events[0][0].event_id == str(uuid5(NAMESPACE_URL, f"paper-submitted:{ORDER_ID}"))
    ingress = admission.calls[0][1]
    assert ingress.action is OrderAction.OPEN
    assert ingress.idempotency_key.startswith("strategy-v2-")
    assert admission.calls[0][2].source.value == "STRATEGY"


def test_paper_bridge_rejects_risk_without_creating_paper_order():
    admission = _Admission(disposition=RuntimeEntryAdmissionDisposition.RISK_REJECTED)
    repository = _Repository()
    receipt = StrategyV2PaperExecutionService(admission_service=admission, repository=repository).persist(
        SimpleNamespace(), _request(), credential_id=9,
    )
    assert receipt.disposition is RuntimeEntryAdmissionDisposition.RISK_REJECTED
    assert receipt.paper_order is None
    assert repository.orders == []
    assert repository.events == []


def test_paper_bridge_is_fail_closed_for_mode_execution_and_reducing_scope():
    service = StrategyV2PaperExecutionService(admission_service=_Admission(), repository=_Repository())
    with pytest.raises(StrategyV2PaperExecutionError, match="paperModeRequired"):
        service.persist(SimpleNamespace(), _request(execution_mode="signal"), credential_id=9)
    with pytest.raises(StrategyV2PaperExecutionError, match="paperTargetPositionRequired"):
        service.persist(SimpleNamespace(), _request(action="close_long"), credential_id=9)
    with pytest.raises(StrategyV2PaperExecutionError, match="paperExecutionUnsupported"):
        service.persist(SimpleNamespace(), _request(order_type="STOP_LIMIT"), credential_id=9)


def test_paper_bridge_rejects_scope_mismatch_before_order_persistence():
    admission = _Admission(scope="persisted-scope")
    repository = _Repository()
    service = StrategyV2PaperExecutionService(admission_service=admission, repository=repository)
    with pytest.raises(StrategyV2PaperExecutionError, match="paperAccountScopeConflict"):
        service.persist(SimpleNamespace(), _request(), credential_id=9, expected_account_scope="caller-scope")
    assert repository.orders == []


def test_paper_bridge_rejects_binary_float_quantity_at_domain_boundary():
    service = StrategyV2PaperExecutionService(admission_service=_Admission(), repository=_Repository())
    with pytest.raises(StrategyV2PaperExecutionError, match="Decimal-compatible"):
        service.persist(SimpleNamespace(), _request(quantity=0.01), credential_id=9)


def test_legacy_strategy_position_resolves_to_one_persisted_uuid_subject():
    subject_id = "22222222-2222-4222-8222-222222222222"
    cursor = _PositionCursor(subject_rows=[(subject_id,)])
    connection = SimpleNamespace(cursor=lambda: cursor)
    resolved = StrategyV2PaperExecutionService._resolve_position_subject(
        connection,
        "123",
        strategy_id=7,
        user_id=12,
        credential_id=9,
        account_scope="paper-scope",
        instrument_id="BTC_USDT",
        market_type="swap",
        position_side=PositionSide.LONG,
    )
    assert resolved == subject_id
    assert len(cursor.queries) == 2
    assert cursor.closed


def test_legacy_strategy_position_rejects_ambiguous_uuid_subjects():
    cursor = _PositionCursor(subject_rows=[
        ("22222222-2222-4222-8222-222222222222",),
        ("33333333-3333-4333-8333-333333333333",),
    ])
    connection = SimpleNamespace(cursor=lambda: cursor)
    with pytest.raises(StrategyV2PaperExecutionError, match="Ambiguous"):
        StrategyV2PaperExecutionService._resolve_position_subject(
            connection,
            "123",
            strategy_id=7,
            user_id=12,
            credential_id=9,
            account_scope="paper-scope",
            instrument_id="BTC_USDT",
            market_type="swap",
            position_side=PositionSide.LONG,
        )
    assert cursor.closed
