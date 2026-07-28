from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
import unittest

from tests.pr08_contract_loader import load_pr08_repository


modules = load_pr08_repository()
s = modules.contracts
r = modules.repository


def result():
    policy = s.ShadowTolerancePolicy("shadow-policy-v1")
    observed = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
    value = s.ShadowFactValue("1", s.ShadowValueKind.QUANTITY, "BTC")
    legacy = s.ShadowSourceSnapshot("legacy", 1, 2, "primary", "BTCUSDT", "swap", "v1", observed, s.ShadowSourceStatus.READY, {"position": value})
    candidate = s.ShadowSourceSnapshot("candidate", 1, 2, "primary", "BTCUSDT", "swap", "v1", observed, s.ShadowSourceStatus.READY, {"position": value}, UUID("22222222-2222-2222-2222-222222222222"), 7)
    run = s.ShadowComparisonRun(
        UUID("11111111-1111-1111-1111-111111111111"), 1, 2, "primary", "BTCUSDT", "swap",
        "legacy", "v1", legacy.source_fingerprint, candidate.generation_id, "shadow-consumer", "b" * 64,
        candidate.checkpoint_watermark, observed, "shadow-corr-1", policy,
    )
    return s.compare_shadow_state(run, legacy, candidate)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, parameters=()):
        self.statements.append((" ".join(statement.split()), parameters))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Connection:
    def __init__(self, rows):
        self.cursor_object = Cursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_object

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ShadowDiffRepositoryTests(unittest.TestCase):
    def _generation_row(self, comparison):
        run = comparison.run
        return (run.candidate_consumer_name, run.candidate_generation_build_fingerprint, "READY", 7, 7, datetime(2026, 7, 26, 9, 1, tzinfo=timezone.utc))

    def _persisted_row(self, comparison, *, legacy_fingerprint=None):
        run = comparison.run
        policy = run.policy
        return (
            run.tenant_id, run.credential_id, run.account_scope, run.instrument_id, run.market_type,
            legacy_fingerprint or comparison.legacy.source_fingerprint, comparison.candidate.source_fingerprint,
            run.legacy_source_identity, run.legacy_source_version, UUID(run.candidate_generation_id),
            run.candidate_consumer_name, run.candidate_generation_build_fingerprint,
            run.candidate_checkpoint_watermark, run.as_of, run.correlation_id, policy.policy_version,
            policy.quantity_absolute, policy.quantity_relative, policy.monetary_absolute,
            policy.monetary_relative, policy.ratio_absolute, policy.tolerance_policy_fingerprint,
            run.build_fingerprint, comparison.replay_fingerprint, "COMPLETE",
        )

    def test_create_is_atomic_boundary_and_never_commits(self):
        comparison = result()
        connection = Connection(rows=[self._generation_row(comparison), ("run",), ("run",)])
        persisted = r.ShadowDiffRepository().persist_comparison(connection, comparison, completed_at=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(persisted.disposition, r.ShadowPersistDisposition.CREATED)
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))
        self.assertIn("INSERT INTO qd_shadow_comparison_runs", connection.cursor_object.statements[1][0])
        self.assertIn("UPDATE qd_shadow_comparison_runs", connection.cursor_object.statements[-1][0])
        insert_sql, insert_parameters = connection.cursor_object.statements[1]
        self.assertEqual(insert_sql.count("%s"), len(insert_parameters))

    def test_completed_identical_run_is_typed_replay_without_commit(self):
        comparison = result()
        persisted_row = self._persisted_row(comparison)
        connection = Connection(rows=[self._generation_row(comparison), None, persisted_row])
        persisted = r.ShadowDiffRepository().persist_comparison(connection, comparison, completed_at=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(persisted.disposition, r.ShadowPersistDisposition.REPLAYED)
        self.assertEqual((connection.commits, connection.rollbacks), (0, 0))

    def test_different_durable_identity_is_typed_conflict(self):
        comparison = result()
        conflicting_row = self._persisted_row(comparison, legacy_fingerprint="c" * 64)
        with self.assertRaises(r.ShadowReplayConflict):
            r.ShadowDiffRepository().persist_comparison(Connection([self._generation_row(comparison), None, conflicting_row]), comparison, completed_at=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc))

    def test_driver_failure_is_typed_and_does_not_commit_or_rollback(self):
        class BrokenConnection:
            def cursor(self):
                raise RuntimeError("driver failure")
        with self.assertRaises(r.ShadowRepositoryError):
            r.ShadowDiffRepository().persist_comparison(BrokenConnection(), result(), completed_at=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc))

    def test_candidate_generation_must_be_ready_and_exact_before_run_insert(self):
        comparison = result()
        for state, source_watermark, processed_watermark, completed in (
            ("BUILDING", 7, 7, None),
            ("FAILED", 7, 7, datetime(2026, 7, 26, 9, 1, tzinfo=timezone.utc)),
            ("READY", 8, 7, datetime(2026, 7, 26, 9, 1, tzinfo=timezone.utc)),
        ):
            row = list(self._generation_row(comparison))
            row[2], row[3], row[4], row[5] = state, source_watermark, processed_watermark, completed
            connection = Connection([tuple(row)])
            with self.assertRaises(r.ShadowRunConflict):
                r.ShadowDiffRepository().persist_comparison(
                    connection, comparison, completed_at=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc)
                )
            self.assertEqual(len(connection.cursor_object.statements), 1)


if __name__ == "__main__":
    unittest.main()
