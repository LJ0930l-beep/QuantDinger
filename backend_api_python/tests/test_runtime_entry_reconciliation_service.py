"""Unit coverage for the minimal reconciliation orchestration service."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest

from app.domain.gate_read_snapshot_contracts import build_gate_read_snapshot
from app.domain.gate_vertical_read_contracts import (
    GateAuthFacts,
    GateInstrumentRuleSnapshot,
    GateMarginMode,
    GatePermission,
    GatePositionFact,
    GatePositionSide,
)
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.domain.reconciliation_contracts import (
    ReconciliationCheckpointStatus,
    ReconciliationDiscrepancyKind,
    ReconciliationSeverity,
)
from app.services.runtime_entry_reconciliation_service import (
    RECONCILIATION_POLICY_VERSION,
    RuntimeEntryReconciliationService,
)


class _Cursor:
    def __init__(self, rows=()):
        self._rows = list(rows)
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append(query)
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _Connection:
    def __init__(self, rows=()):
        self.cursor_instance = _Cursor(rows)

    def cursor(self):
        return self.cursor_instance


def _snapshot(observed=None, positions=()):
    observed = observed or datetime(2026, 8, 5, tzinfo=timezone.utc)
    auth = GateAuthFacts(
        "gate", AssetMarketType.PERPETUAL, CapabilityEnvironment.TESTNET, "account-1",
        "credential-1", (GatePermission.READ_ACCOUNT,), "gate-private-read-v1", observed,
    )
    instruments = (
        GateInstrumentRuleSnapshot(
            "gate", AssetMarketType.PERPETUAL, "BTC_USDT", Decimal("0.1"), Decimal("0.000001"),
            Decimal("0.00001"), Decimal("3"), "gate-private-read-instrument-v1", observed,
        ),
    )
    return build_gate_read_snapshot(auth, balances=(), instruments=instruments, positions=positions, observed_at=observed)


def _position(quantity, side=GatePositionSide.LONG):
    observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
    return GatePositionFact(
        "gate", AssetMarketType.PERPETUAL, "account-1", "BTC_USDT", side, Decimal(quantity),
        Decimal("60000"), Decimal("60100"), Decimal("10"), GateMarginMode.ISOLATED,
        observed, "event-1",
    )


class RuntimeEntryReconciliationServiceTests(unittest.TestCase):
    def test_both_empty_yields_healthy_zero_discrepancies(self):
        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        snapshot = _snapshot(observed, positions=())
        service = RuntimeEntryReconciliationService(
            snapshot_provider=lambda *a, **k: snapshot,
            local_position_reader=lambda *a, **k: {},
        )
        result = service.run_reconciliation(
            _Connection(), user_id=3, credential_id=1, account_scope="account-1",
            market_type="perpetual", instrument_id="BTC_USDT", as_of=observed,
        )
        self.assertEqual(result.checkpoint.status, ReconciliationCheckpointStatus.HEALTHY)
        self.assertEqual(len(result.discrepancies), 0)
        self.assertEqual(result.run.policy.policy_version, RECONCILIATION_POLICY_VERSION)

    def test_local_fill_mismatch_degrades_health(self):
        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        snapshot = _snapshot(observed, positions=(_position("0.01"),))
        service = RuntimeEntryReconciliationService(
            snapshot_provider=lambda *a, **k: snapshot,
            local_position_reader=lambda *a, **k: {("BTC_USDT", "LONG"): Decimal("0")},
        )
        result = service.run_reconciliation(
            _Connection(), user_id=3, credential_id=1, account_scope="account-1",
            market_type="perpetual", instrument_id="BTC_USDT", as_of=observed,
        )
        self.assertNotEqual(result.checkpoint.status, ReconciliationCheckpointStatus.HEALTHY)
        self.assertEqual(len(result.discrepancies), 1)
        self.assertEqual(result.discrepancies[0].kind, ReconciliationDiscrepancyKind.POSITION_MISMATCH)

    def test_external_only_position_mismatch(self):
        observed = datetime(2026, 8, 5, tzinfo=timezone.utc)
        snapshot = _snapshot(observed, positions=(_position("0.005"),))
        service = RuntimeEntryReconciliationService(
            snapshot_provider=lambda *a, **k: snapshot,
            local_position_reader=lambda *a, **k: {},
        )
        result = service.run_reconciliation(
            _Connection(), user_id=3, credential_id=1, account_scope="account-1",
            market_type="perpetual", instrument_id="BTC_USDT", as_of=observed,
        )
        self.assertNotEqual(result.checkpoint.status, ReconciliationCheckpointStatus.HEALTHY)
        kinds = {item.kind for item in result.discrepancies}
        self.assertIn(ReconciliationDiscrepancyKind.POSITION_MISMATCH, kinds)

    def test_fail_closed_when_provider_raises(self):
        from app.services.runtime_entry_reconciliation_service import RuntimeEntryReconciliationError

        service = RuntimeEntryReconciliationService(
            snapshot_provider=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("network down")),
        )
        with self.assertRaises(RuntimeEntryReconciliationError):
            service.run_reconciliation(
                _Connection(), user_id=3, credential_id=1, account_scope="account-1",
                market_type="perpetual", instrument_id="BTC_USDT",
            )


if __name__ == "__main__":
    unittest.main()
