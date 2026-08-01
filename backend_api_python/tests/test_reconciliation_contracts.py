from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest
from uuid import UUID

from tests.pr09_contract_loader import load_pr09_contracts


s = load_pr09_contracts()
RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
GENERATION_ID = UUID("22222222-2222-2222-2222-222222222222")
OBSERVED = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)
AS_OF = datetime(2026, 7, 28, 9, 1, tzinfo=timezone.utc)
SHA = "a" * 64


def quantity(value, asset="BTC"):
    return s.ReconciliationFactValue(value, s.ReconciliationFactKind.QUANTITY, asset)


def money(value, asset="USDT"):
    return s.ReconciliationFactValue(value, s.ReconciliationFactKind.MONETARY, asset)


def source(identity, facts, **changes):
    values = {
        "source_identity": identity, "source_version": "facts-v1", "tenant_id": 1,
        "credential_id": 2, "account_scope": "primary", "venue": "binance", "market_type": "swap",
        "instrument_id": "BTCUSDT", "asset_scope": None, "observed_at": OBSERVED, "facts": facts,
    }
    if identity == "local":
        values.update({"generation_id": GENERATION_ID, "checkpoint_watermark": 7})
    values.update(changes)
    return s.ReconciliationSourceSnapshot(**values)


def run(external, **changes):
    values = {
        "run_id": RUN_ID, "tenant_id": 1, "credential_id": 2, "account_scope": "primary",
        "venue": "binance", "market_type": "swap", "instrument_id": "BTCUSDT", "asset_scope": None,
        "local_generation_id": GENERATION_ID, "local_consumer_name": "ledger",
        "local_generation_build_fingerprint": SHA, "local_checkpoint_watermark": 7,
        "external_observation_identity": external.source_identity,
        "external_observation_version": external.source_version,
        "external_observation_fingerprint": external.source_fingerprint,
        "local_observed_at": OBSERVED, "external_observed_at": external.observed_at,
        "as_of": AS_OF, "correlation_id": "audit-correlation",
        "policy": s.ReconciliationPolicySnapshot("reconciliation-policy-v1", True),
    }
    values.update(changes)
    return s.ReconciliationRun(**values)


class ReconciliationContractTests(unittest.TestCase):
    def test_consistent_facts_derive_healthy_checkpoint(self):
        external = source("venue", {"position": quantity("1"), "equity": money("100")})
        local = source("local", {"position": quantity("1"), "equity": money("100")})
        result = s.compare_reconciliation_state(run(external), local, external)
        self.assertEqual(result.discrepancies, ())
        self.assertEqual(result.checkpoint.status, s.ReconciliationCheckpointStatus.HEALTHY)
        self.assertEqual(result.checkpoint.health, s.ReconciliationHealth.HEALTHY)

    def test_warning_only_is_degraded_when_policy_requires_it(self):
        external = source("venue", {"position": quantity("1")})
        local = source("local", {"position": quantity("2")})
        result = s.compare_reconciliation_state(run(external), local, external)
        self.assertEqual(result.discrepancies[0].severity, s.ReconciliationSeverity.WARNING)
        self.assertEqual(result.checkpoint.health, s.ReconciliationHealth.DEGRADED)

    def test_blocking_unknown_and_stale_never_report_healthy(self):
        external = source("venue", {"position": quantity("1")})
        local = source("local", {})
        result = s.compare_reconciliation_state(run(external), local, external)
        self.assertEqual(result.checkpoint.health, s.ReconciliationHealth.UNHEALTHY)
        unknown = s.ReconciliationResult(run(external), source("local", {"position": quantity("1")}), external, (
            s.ReconciliationDiscrepancy("submission", s.ReconciliationDiscrepancyKind.UNKNOWN_SUBMISSION, s.ReconciliationSeverity.WARNING, detail="unknown"),
        ))
        self.assertEqual(unknown.checkpoint.health, s.ReconciliationHealth.UNHEALTHY)
        stale_policy = s.ReconciliationPolicySnapshot("reconciliation-policy-v1", True, max_observation_age_seconds=10)
        stale_run = run(external, policy=stale_policy, as_of=OBSERVED + timedelta(seconds=11))
        stale = s.compare_reconciliation_state(stale_run, source("local", {"position": quantity("1")}), external)
        self.assertEqual(stale.checkpoint.health, s.ReconciliationHealth.UNHEALTHY)

    def test_scope_and_asset_mismatch_fail_closed(self):
        external = source("venue", {"equity": money("100", "USDT")})
        local = source("local", {"equity": money("100", "USDC")})
        result = s.compare_reconciliation_state(run(external), local, external)
        self.assertEqual(result.discrepancies[0].kind, s.ReconciliationDiscrepancyKind.SCOPE_MISMATCH)
        other = source("venue", {"equity": money("100")}, account_scope="other")
        with self.assertRaises(s.ReconciliationContractError):
            s.compare_reconciliation_state(run(other), local, other)

    def test_policy_changes_identity_but_correlation_is_audit_only(self):
        external = source("venue", {"position": quantity("1")})
        first = run(external)
        changed_policy = run(external, policy=s.ReconciliationPolicySnapshot("reconciliation-policy-v1", True, quantity_absolute="0.1"))
        changed_correlation = run(external, correlation_id="second-correlation")
        self.assertNotEqual(first.build_fingerprint, changed_policy.build_fingerprint)
        self.assertEqual(first.build_fingerprint, changed_correlation.build_fingerprint)
        local = source("local", {"position": quantity(Decimal("1"))})
        self.assertEqual(
            s.compare_reconciliation_state(first, local, external).replay_fingerprint,
            s.compare_reconciliation_state(changed_correlation, local, external).replay_fingerprint,
        )

    def test_float_and_incomplete_contracts_are_rejected(self):
        with self.assertRaises(s.ReconciliationContractError):
            quantity(1.0)
        with self.assertRaises(s.ReconciliationContractError):
            source("venue", {"position": quantity("1")}, instrument_id=None, asset_scope=None)
        external = source("venue", {"position": quantity("1")})
        with self.assertRaises(s.ReconciliationContractError):
            run(external, external_observation_fingerprint="bad")

    def test_local_generation_and_checkpoint_watermark_are_bound(self):
        external = source("venue", {"position": quantity("1")})
        local = source("local", {"position": quantity("1")}, checkpoint_watermark=8)
        with self.assertRaises(s.ReconciliationContractError):
            s.compare_reconciliation_state(run(external), local, external)
        local_with_wrong_generation = source(
            "local", {"position": quantity("1")},
            generation_id=UUID("33333333-3333-3333-3333-333333333333"),
        )
        with self.assertRaises(s.ReconciliationContractError):
            s.compare_reconciliation_state(run(external), local_with_wrong_generation, external)

    def test_external_source_cannot_claim_local_generation(self):
        external = source(
            "venue", {"position": quantity("1")},
            generation_id=GENERATION_ID, checkpoint_watermark=7,
        )
        local = source("local", {"position": quantity("1")})
        with self.assertRaises(s.ReconciliationContractError):
            s.compare_reconciliation_state(run(external), local, external)

    def test_empty_both_sources_fail_closed_to_unhealthy(self):
        external = source("venue", {})
        local = source("local", {})
        result = s.compare_reconciliation_state(run(external), local, external)
        self.assertEqual(result.checkpoint.status, s.ReconciliationCheckpointStatus.CONFLICT)
        self.assertEqual(result.checkpoint.health, s.ReconciliationHealth.UNHEALTHY)

    def test_duplicate_discrepancy_facts_are_rejected(self):
        external = source("venue", {"position": quantity("1")})
        local = source("local", {"position": quantity("1")})
        discrepancy = s.ReconciliationDiscrepancy(
            "position", s.ReconciliationDiscrepancyKind.POSITION_MISMATCH,
            s.ReconciliationSeverity.WARNING, quantity("1"), quantity("2"), "value_mismatch",
        )
        with self.assertRaises(s.ReconciliationContractError):
            s.ReconciliationResult(run(external), local, external, (discrepancy, discrepancy))

    def test_fingerprint_is_stable_for_decimal_and_mapping_order(self):
        external_one = source("venue", {"position": quantity("1.000000000000000000"), "equity": money("100")})
        local_one = source("local", {"position": quantity("1"), "equity": money("100.00")})
        external_two = source("venue", {"equity": money("100.00"), "position": quantity("1")})
        local_two = source("local", {"equity": money("100"), "position": quantity("1.000000000000000000")})
        first = s.compare_reconciliation_state(run(external_one), local_one, external_one)
        second = s.compare_reconciliation_state(run(external_two), local_two, external_two)
        self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)


if __name__ == "__main__":
    unittest.main()
