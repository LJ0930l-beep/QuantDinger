from __future__ import annotations

from decimal import Decimal
import uuid

import pytest

from app.domain.gate_testnet_execution_contracts import GateExecutionKind, GateOrderSide, GateTestnetExecutionRequest
from app.domain.gate_vertical_read_contracts import GateFillFact
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.domain.order_state_machine import SubmissionAttemptScope
from app.services.exchange_order_repository import (
    ExchangeOrderConflict,
    ExchangeOrderRepository,
    facts_from_gate_receipt,
    normalized_gate_state,
)
from app.services.gate_testnet_order_client import GateTestnetOrderReceipt


class Cursor:
    def __init__(self, inserted=None, rows=()):
        self.inserted = inserted
        self.rows = list(rows)
        self.closed = False

    def execute(self, query, params=()):
        pass

    def fetchone(self):
        value, self.inserted = self.inserted, None
        return value

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _facts():
    scope = SubmissionAttemptScope(1, 2, "account", "BTC_USDT", "spot", str(uuid.uuid4()), "gate")
    request = GateTestnetExecutionRequest(
        instrument_id="BTC_USDT", market_type=AssetMarketType.SPOT, account_scope="account",
        side=GateOrderSide.BUY, quantity=Decimal("1"), reference_price=Decimal("100"),
        execution_kind=GateExecutionKind.MARKET, client_order_id="t-case", observed_at=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc), environment=CapabilityEnvironment.TESTNET,
    )
    receipt = GateTestnetOrderReceipt(
        market_type=AssetMarketType.SPOT, account_scope="account", instrument_id="BTC_USDT",
        client_order_id="t-case", exchange_order_id="venue-1", raw_state="filled", status_code=200,
        response_fingerprint="response-1",
    )
    return facts_from_gate_receipt(receipt, request, scope=scope, attempt_id=str(uuid.uuid4()), attempt_row_id=str(uuid.uuid4()))


def _row(f):
    return (f.id, f.attempt_id, f.economic_order_id, f.child_role, f.scope.exchange, f.scope.tenant_id, f.scope.credential_id,
            f.scope.market_type, f.scope.account_scope, f.scope.instrument_id, f.exchange_order_id, f.venue_client_order_id,
            f.raw_status, f.normalized_state.value, f.requested_qty, f.raw_payload_hash)


def test_unknown_gate_state_fails_closed():
    with pytest.raises(Exception):
        normalized_gate_state("mystery")


def test_receipt_facts_preserve_decimal_and_scope():
    facts = _facts()
    assert facts.normalized_state.value == "FILLED"
    assert facts.requested_qty == Decimal("1")
    assert facts.scope.exchange == "gate"


def test_caller_owned_insert_and_exact_replay():
    facts = _facts()
    first = Connection(Cursor(inserted=(facts.id,)))
    assert ExchangeOrderRepository().persist_caller_owned(first, facts)[1] == "APPLIED"
    assert first.commits == 0 and first.rollbacks == 0
    replay = Connection(Cursor(rows=[_row(facts)]))
    assert ExchangeOrderRepository().persist_caller_owned(replay, facts)[1] == "REPLAYED"


def test_immutable_exchange_identity_conflict():
    facts = _facts()
    row = list(_row(facts)); row[10] = "venue-other"
    with pytest.raises(ExchangeOrderConflict):
        ExchangeOrderRepository().persist_caller_owned(Connection(Cursor(rows=[tuple(row)])), facts)
