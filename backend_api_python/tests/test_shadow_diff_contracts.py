from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import unittest
from uuid import UUID

from tests.pr08_contract_loader import load_pr08_contracts


s = load_pr08_contracts()
RUN_ID = UUID("11111111-1111-1111-1111-111111111111")


def policy(**changes):
    values = {"policy_version": "shadow-policy-v1", "quantity_absolute": "0.01", "monetary_absolute": "0.10", "ratio_absolute": "0.001"}
    values.update(changes)
    return s.ShadowTolerancePolicy(**values)


def run(**changes):
    values = {"run_id": RUN_ID, "tenant_id": 1, "credential_id": 2, "account_scope": "primary", "instrument_id": "BTCUSDT", "market_type": "swap", "policy": policy(), "build_fingerprint": "a" * 64}
    values.update(changes)
    return s.ShadowComparisonRun(**values)


def snapshot(source_name, facts, **changes):
    values = {"source_name": source_name, "tenant_id": 1, "credential_id": 2, "account_scope": "primary", "instrument_id": "BTCUSDT", "market_type": "swap", "source_version": "state-v1", "observed_at": datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc), "status": s.ShadowSourceStatus.READY, "facts": facts}
    values.update(changes)
    return s.ShadowSourceSnapshot(**values)


def quantity(value, asset="BTC"):
    return s.ShadowFactValue(value, s.ShadowValueKind.QUANTITY, asset)


def money(value, asset="USDT"):
    return s.ShadowFactValue(value, s.ShadowValueKind.MONETARY, asset)


class ShadowDiffContractTests(unittest.TestCase):
    def test_exact_and_tolerated_matches_are_distinct_and_replay_is_deterministic(self):
        legacy = snapshot("legacy", {"position": quantity("1"), "equity": money("100")})
        candidate = snapshot("candidate", {"position": quantity("1.005"), "equity": money("100")})
        first = s.compare_shadow_state(run(), legacy, candidate)
        second = s.compare_shadow_state(run(), legacy, candidate)
        self.assertEqual(first.exact_matches, ("equity",))
        self.assertEqual(first.tolerated_matches, ("position",))
        self.assertEqual(first.diffs, ())
        self.assertEqual(first.replay_fingerprint, second.replay_fingerprint)

    def test_currency_mismatch_requires_valuation_and_never_matches(self):
        result = s.compare_shadow_state(run(), snapshot("legacy", {"equity": money("100", "USDT")}), snapshot("candidate", {"equity": money("100", "USDC")}))
        self.assertEqual(result.exact_matches, ())
        self.assertEqual(result.diffs[0].kind, s.ShadowDiffKind.VALUATION_REQUIRED)
        self.assertEqual(result.diffs[0].severity, s.ShadowDiffSeverity.BLOCKING)

    def test_unknown_stale_and_scope_mismatch_fail_closed(self):
        legacy = snapshot("legacy", {"position": quantity("1")}, status=s.ShadowSourceStatus.STALE)
        candidate = snapshot("candidate", {"position": quantity("1")})
        self.assertEqual(s.compare_shadow_state(run(), legacy, candidate).diffs[0].kind, s.ShadowDiffKind.STALE_SOURCE)
        cross_scope = snapshot("candidate", {"position": quantity("1")}, account_scope="other")
        self.assertEqual(s.compare_shadow_state(run(), candidate, cross_scope).diffs[0].kind, s.ShadowDiffKind.SCOPE_MISMATCH)

    def test_missing_value_kind_and_outside_tolerance_are_explicit(self):
        legacy = snapshot("legacy", {"a": quantity("1"), "b": quantity("1"), "c": money("1")})
        candidate = snapshot("candidate", {"a": quantity("1"), "b": quantity("1.02"), "c": quantity("1", "USDT"), "d": quantity("1")})
        result = s.compare_shadow_state(run(), legacy, candidate)
        self.assertEqual(result.exact_matches, ("a",))
        self.assertEqual({item.kind for item in result.diffs}, {s.ShadowDiffKind.VALUE_MISMATCH, s.ShadowDiffKind.UNSUPPORTED_FACT, s.ShadowDiffKind.MISSING_LEGACY})

    def test_float_and_noncanonical_values_are_rejected(self):
        with self.assertRaises(s.ShadowDiffContractError):
            quantity(1.0)
        with self.assertRaises(s.ShadowDiffContractError):
            snapshot("Legacy", {"position": quantity("1")})
        with self.assertRaises(s.ShadowDiffContractError):
            run(build_fingerprint="not-a-fingerprint")

    def test_snapshot_and_result_are_immutable(self):
        fact = quantity(Decimal("1"))
        source = snapshot("legacy", {"position": fact})
        with self.assertRaises(TypeError):
            source.facts["other"] = fact
        result = s.compare_shadow_state(run(), source, snapshot("candidate", {"position": fact}))
        with self.assertRaises(Exception):
            result.exact_matches += ("other",)


if __name__ == "__main__":
    unittest.main()
