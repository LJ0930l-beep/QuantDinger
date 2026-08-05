from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.readonly_shadow_summary_contracts import (
    ReadonlyShadowComparisonSummary,
    ReadonlyShadowSummaryError,
)
from app.services.readonly_shadow_repository import ReadonlyShadowRepository
from app.services.readonly_shadow_summary_service import (
    ReadonlyShadowSummaryService,
    ReadonlyShadowSummaryServiceError,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
RUN_ID = "11111111-1111-1111-1111-111111111111"
GEN_ID = "22222222-2222-2222-2222-222222222222"
SHA = "a" * 64


def _summary(**changes):
    values = dict(
        run_id=RUN_ID,
        credential_id=7,
        exchange="gate",
        market_type="spot",
        account_scope="acct-1",
        instrument_id="BTC_USDT",
        candidate_generation_id=GEN_ID,
        candidate_consumer_name="candidate",
        candidate_generation_build_fingerprint=SHA,
        candidate_checkpoint_watermark=12,
        as_of=NOW,
        tolerance_policy_version="shadow-tolerance-v1",
        quantity_absolute=Decimal("0.01"),
        quantity_relative=Decimal("0.001"),
        monetary_absolute=Decimal("1"),
        monetary_relative=Decimal("0.001"),
        ratio_absolute=Decimal("0.0001"),
        tolerance_policy_fingerprint=SHA,
        build_fingerprint="b" * 64,
        replay_fingerprint="c" * 64,
        completed_at=NOW,
        diff_count=0,
        blocking_diff_count=0,
    )
    values.update(changes)
    return ReadonlyShadowComparisonSummary(**values)


def test_summary_is_scoped_and_serializes_decimal_facts():
    result = _summary(diff_count=2, blocking_diff_count=1)
    body = result.to_public_dict()
    assert result.match_status == "BLOCKING"
    assert body["quantity_absolute"] == "0.01"
    assert body["live_enabled"] is False
    with pytest.raises(ReadonlyShadowSummaryError):
        _summary(exchange="GATE")
    with pytest.raises(ReadonlyShadowSummaryError):
        _summary(blocking_diff_count=3)


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
    row = (
        RUN_ID, 7, "gate", "spot", "acct-1", "BTC_USDT", GEN_ID, "candidate", SHA, 12,
        NOW, "shadow-tolerance-v1", Decimal("0.01"), Decimal("0.001"), Decimal("1"),
        Decimal("0.001"), Decimal("0.0001"), SHA, "b" * 64, "c" * 64, NOW, 2, 1,
    )
    conn = _Connection(row)
    result = ReadonlyShadowRepository().read_latest(
        conn, user_id=3, credential_id=7, exchange="gate", market_type="spot",
        account_scope="acct-1", instrument_id="BTC_USDT", as_of=NOW,
    )
    assert result is not None and result.run_id == RUN_ID
    assert result.match_status == "BLOCKING"
    assert conn.commits == conn.rollbacks == 0
    assert conn.cursor_value.closed is True
    assert "state = 'COMPLETE'" in conn.cursor_value.calls[0][0]
    assert "qd_exchange_credentials" in conn.cursor_value.calls[0][0]


def test_repository_missing_row_is_unavailable_without_writes():
    conn = _Connection(None)
    assert ReadonlyShadowRepository().read_latest(
        conn, user_id=3, credential_id=7, exchange="gate", market_type="spot",
        account_scope="acct-1", instrument_id="BTC_USDT", as_of=NOW,
    ) is None
    assert conn.commits == conn.rollbacks == 0
    assert conn.cursor_value.closed is True


def test_service_rejects_untyped_provider_result():
    service = ReadonlyShadowSummaryService(provider=lambda *args: {})
    with pytest.raises(ReadonlyShadowSummaryServiceError):
        service.read_response(
            user_id=3, credential_id=7, exchange="gate", market_type="spot",
            account_scope="acct-1", instrument_id="BTC_USDT", as_of=NOW,
        )
