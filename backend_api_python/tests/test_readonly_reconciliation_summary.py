from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.domain.order_contracts import ReconciliationCheckpointStatus, ReconciliationHealth
from app.domain.readonly_reconciliation_summary_contracts import (
    ReadonlyReconciliationCheckpointSummary,
    ReadonlyReconciliationSummaryError,
)
from app.services.readonly_reconciliation_repository import ReadonlyReconciliationRepository
from app.services.readonly_reconciliation_summary_service import (
    ReadonlyReconciliationSummaryService,
    ReadonlyReconciliationSummaryServiceError,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CHECKPOINT = "11111111-1111-1111-1111-111111111111"


def _summary(**changes):
    values = dict(
        checkpoint_id=CHECKPOINT,
        credential_id=7,
        exchange="gate",
        market_type="spot",
        account_scope="acct-1",
        instrument_id="BTC_USDT",
        status=ReconciliationCheckpointStatus.HEALTHY,
        unresolved_count=0,
        version=2,
        last_success_at=NOW,
        sla_deadline=NOW + timedelta(minutes=5),
        updated_at=NOW,
        as_of=NOW,
    )
    values.update(changes)
    return ReadonlyReconciliationCheckpointSummary(**values)


def test_summary_derives_health_from_status_and_sla_only():
    assert _summary().derived_health is ReconciliationHealth.HEALTHY
    assert _summary(sla_deadline=NOW).derived_health is ReconciliationHealth.DEGRADED
    assert _summary(status="STALE").derived_health is ReconciliationHealth.DEGRADED
    assert _summary(status="FAILED").derived_health is ReconciliationHealth.UNHEALTHY
    assert _summary(status="CONFLICT").derived_health is ReconciliationHealth.UNHEALTHY
    assert _summary().to_public_dict()["live_enabled"] is False


def test_summary_rejects_noncanonical_scope_and_typed_status():
    with pytest.raises(ReadonlyReconciliationSummaryError):
        _summary(exchange="GATE")
    with pytest.raises(ReadonlyReconciliationSummaryError):
        _summary(status="DEGRADED")
    with pytest.raises(ReadonlyReconciliationSummaryError):
        _summary(checkpoint_id="not-a-uuid")


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.closed = False
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, row):
        self.cursor_value = _Cursor(row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value


def test_repository_is_select_only_and_scope_bound():
    row = (CHECKPOINT, 7, "gate", "spot", "acct-1", "BTC_USDT", "HEALTHY", 0, 2, NOW, NOW + timedelta(minutes=5), NOW)
    conn = _Connection(row)
    result = ReadonlyReconciliationRepository().read_checkpoint(
        conn,
        user_id=3,
        credential_id=7,
        exchange="gate",
        market_type="spot",
        account_scope="acct-1",
        instrument_id="BTC_USDT",
        as_of=NOW,
    )
    assert result is not None and result.checkpoint_id == str(UUID(CHECKPOINT))
    assert conn.commits == conn.rollbacks == 0
    assert conn.cursor_value.closed is True
    assert len(conn.cursor_value.calls) == 1
    assert "encrypted" not in conn.cursor_value.calls[0][0].lower()


def test_repository_missing_scope_is_unavailable_without_writes():
    conn = _Connection(None)
    assert ReadonlyReconciliationRepository().read_checkpoint(
        conn,
        user_id=3,
        credential_id=7,
        exchange="gate",
        market_type="spot",
        account_scope="acct-1",
        instrument_id="BTC_USDT",
        as_of=NOW,
    ) is None
    assert conn.commits == conn.rollbacks == 0
    assert conn.cursor_value.closed is True


def test_service_requires_typed_provider_result():
    service = ReadonlyReconciliationSummaryService(provider=lambda *args: {})
    with pytest.raises(ReadonlyReconciliationSummaryServiceError):
        service.read_response(
            user_id=3, credential_id=7, exchange="gate", market_type="spot",
            account_scope="acct-1", instrument_id="BTC_USDT", as_of=NOW,
        )
