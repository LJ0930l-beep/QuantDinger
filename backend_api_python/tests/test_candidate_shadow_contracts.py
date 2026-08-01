"""Pure Candidate Projection generation and Shadow binding tests."""

from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from uuid import UUID


_MISSING = object()


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_contracts() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts", "app.domain.canonical_entry_v2_contracts",
        "app.domain.durable_entry_persistence_contracts", "app.domain.hard_risk_contracts",
        "app.domain.durable_risk_enforcement_v2_contracts", "app.domain.authoritative_risk_facts_contracts",
        "app.domain.outbox_projection_contracts", "app.domain.entry_admission_v2_contracts",
        "app.domain.projection_mapping_contracts", "app.domain.projection_consumer_contracts",
        "app.domain.shadow_diff_contracts", "app.domain.candidate_shadow_contracts",
    )
    previous = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        modules = {}
        for name in names[2:]:
            modules[name.rsplit(".", 1)[-1]] = _load(name, app_dir / "domain" / f"{name.rsplit('.', 1)[-1]}.py")
        return SimpleNamespace(**modules)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


m = _load_contracts()
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
GENERATION_ID = UUID("22222222-2222-2222-2222-222222222222")
RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
BUILD = "a" * 64


def _hash(seed: str) -> str:
    return (seed * 64)[:64]


def _payload() -> dict:
    return {
        "admission_contract_version": "entry-admission-v2", "command_id": "11111111-1111-1111-1111-111111111111",
        "action": "OPEN", "risk_effect": "INCREASE_RISK", "subject_kind": "ECONOMIC_ORDER",
        "subject_id": "44444444-4444-4444-4444-444444444444", "cancel_target_kind": None,
        "economic_order_id": "44444444-4444-4444-4444-444444444444", "economic_fingerprint": _hash("a"),
        "request_fingerprint": _hash("b"), "tenant_id": 7, "credential_id": 8, "account_scope": "paper-main",
        "instrument_id": "BTC-USDT", "market_type": "swap", "actor_type": "STRATEGY", "actor_id": "strategy-1",
        "source": "STRATEGY", "mode": "PAPER", "correlation_id": "corr-1", "occurred_at": NOW.isoformat(),
        "risk_decision_id": "55555555-5555-5555-5555-555555555555", "risk_decision_status": "ALLOW",
        "decision_fingerprint": _hash("c"), "scope_fingerprint": _hash("d"), "audit_fingerprint": _hash("e"),
        "reservation_id": "66666666-6666-6666-6666-666666666666",
    }


def _event():
    return m.outbox_projection_contracts.OutboxEvent(
        "DURABLE_ECONOMIC_ORDER", "44444444-4444-4444-4444-444444444444", 0,
        "DURABLE_ENTRY_ADMITTED", "entry-admission-v2", _payload(),
    )


def _consumer():
    return m.projection_consumer_contracts.RegisteredProjectionConsumer(
        "candidate-consumer", "projection-consumer-v1",
        (("DURABLE_ENTRY_ADMITTED", "entry-admission-v2"),),
        ("DURABLE_ECONOMIC_ORDER",), BUILD,
    )


def _generation_view(**changes):
    values = {
        "generation_id": str(GENERATION_ID), "consumer_name": "candidate-consumer", "build_fingerprint": BUILD,
        "source_high_watermark": 7, "processed_high_watermark": 7, "state": "READY",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _snapshot(source_name="candidate", **changes):
    values = {
        "source_name": source_name, "tenant_id": 7, "credential_id": 8, "account_scope": "paper-main",
        "instrument_id": "BTC-USDT", "market_type": "swap", "source_version": "candidate-v1",
        "observed_at": NOW, "status": m.shadow_diff_contracts.ShadowSourceStatus.READY,
        "facts": {"equity": m.shadow_diff_contracts.ShadowFactValue("100", m.shadow_diff_contracts.ShadowValueKind.MONETARY, "USDT")},
        "generation_id": GENERATION_ID if source_name == "candidate" else None,
        "checkpoint_watermark": 7 if source_name == "candidate" else None,
    }
    values.update(changes)
    return m.shadow_diff_contracts.ShadowSourceSnapshot(**values)


def _candidate():
    mapped = m.projection_mapping_contracts.map_admission_outbox_to_projection(_event())
    binding = m.candidate_shadow_contracts.CandidateGenerationBinding.from_generation(_generation_view(), _consumer())
    return m.candidate_shadow_contracts.CandidateProjectionGeneration(binding, (mapped,), _snapshot())


def _run(candidate, **changes):
    legacy = _snapshot("legacy")
    values = {
        "run_id": RUN_ID, "tenant_id": 7, "credential_id": 8, "account_scope": "paper-main", "instrument_id": "BTC-USDT",
        "market_type": "swap", "legacy_source_identity": "legacy", "legacy_source_version": "candidate-v1",
        "legacy_source_fingerprint": legacy.source_fingerprint, "candidate_generation_id": GENERATION_ID,
        "candidate_consumer_name": "candidate-consumer", "candidate_generation_build_fingerprint": BUILD,
        "candidate_checkpoint_watermark": 7, "as_of": NOW, "correlation_id": "shadow-corr",
        "policy": m.shadow_diff_contracts.ShadowTolerancePolicy("policy-v1", quantity_absolute="0.01", monetary_absolute="0.10", ratio_absolute="0.001"),
    }
    values.update(changes)
    return m.shadow_diff_contracts.ShadowComparisonRun(**values)


class CandidateShadowContractTests(unittest.TestCase):
    def test_generation_binding_requires_ready_exact_consumer_and_watermark(self):
        binding = m.candidate_shadow_contracts.CandidateGenerationBinding.from_generation(_generation_view(), _consumer())
        self.assertEqual(binding.checkpoint_watermark, 7)
        with self.assertRaises(m.candidate_shadow_contracts.CandidateGenerationConflict):
            m.candidate_shadow_contracts.CandidateGenerationBinding.from_generation(_generation_view(state="BUILDING"), _consumer())
        with self.assertRaises(m.candidate_shadow_contracts.CandidateGenerationConflict):
            m.candidate_shadow_contracts.CandidateGenerationBinding.from_generation(_generation_view(processed_high_watermark=6), _consumer())

    def test_candidate_generation_is_immutable_and_deterministic(self):
        first, second = _candidate(), _candidate()
        self.assertEqual(first.projection_fingerprint, second.projection_fingerprint)
        self.assertEqual([item.event_id for item in first.facts], [item.event_id for item in second.facts])
        with self.assertRaises(Exception):
            first.facts += ()

    def test_candidate_facts_and_snapshot_scope_must_match(self):
        mapped = m.projection_mapping_contracts.map_admission_outbox_to_projection(_event())
        binding = m.candidate_shadow_contracts.CandidateGenerationBinding.from_generation(_generation_view(), _consumer())
        with self.assertRaises(m.candidate_shadow_contracts.CandidateGenerationConflict):
            m.candidate_shadow_contracts.CandidateProjectionGeneration(binding, (mapped,), _snapshot(account_scope="other"))

    def test_shadow_binding_requires_exact_generation_facts_and_is_read_only(self):
        candidate = _candidate()
        bound = m.candidate_shadow_contracts.bind_candidate_shadow(_run(candidate), candidate)
        self.assertTrue(bound.binding_fingerprint)
        result = m.candidate_shadow_contracts.compare_bound_candidate_shadow(bound, _snapshot("legacy"))
        self.assertEqual(result.diffs, ())
        with self.assertRaises(m.candidate_shadow_contracts.CandidateGenerationConflict):
            m.candidate_shadow_contracts.bind_candidate_shadow(_run(candidate, candidate_checkpoint_watermark=8), candidate)
        self.assertFalse(hasattr(bound, "admit"))

    def test_different_generation_build_or_consumer_fingerprint_does_not_replay(self):
        candidate = _candidate()
        changed = _run(candidate, candidate_generation_build_fingerprint="b" * 64)
        with self.assertRaises(m.candidate_shadow_contracts.CandidateGenerationConflict):
            m.candidate_shadow_contracts.bind_candidate_shadow(changed, candidate)


if __name__ == "__main__":
    unittest.main()
