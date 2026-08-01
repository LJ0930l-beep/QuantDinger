"""Pure G4-B chain validation tests (no Flask, database, or network)."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace
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


def _modules() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts", "app.domain.canonical_entry_v2_contracts",
        "app.domain.durable_entry_persistence_contracts", "app.domain.hard_risk_contracts",
        "app.domain.durable_risk_enforcement_v2_contracts", "app.domain.authoritative_risk_facts_contracts",
        "app.domain.outbox_projection_contracts", "app.domain.entry_admission_v2_contracts",
        "app.domain.projection_mapping_contracts", "app.domain.projection_consumer_contracts",
        "app.domain.shadow_diff_contracts", "app.domain.candidate_shadow_contracts",
        "app.domain.reconciliation_contracts", "app.domain.g4b_readonly_contracts",
    )
    previous = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        loaded = {}
        for name in names[2:]:
            short = name.rsplit(".", 1)[-1]
            loaded[short] = _load(name, app_dir / "domain" / f"{short}.py")
        return SimpleNamespace(**loaded)
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


m = _modules()
NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
GENERATION_ID = UUID("22222222-2222-2222-2222-222222222222")
RUN_ID = UUID("33333333-3333-3333-3333-333333333333")
BUILD = "a" * 64


def _hash(seed: str) -> str:
    return (seed * 64)[:64]


def _event() -> object:
    payload = {
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
    return m.outbox_projection_contracts.OutboxEvent(
        "DURABLE_ECONOMIC_ORDER", "44444444-4444-4444-4444-444444444444", 0,
        "DURABLE_ENTRY_ADMITTED", "entry-admission-v2", payload,
    )


def _consumer():
    return m.projection_consumer_contracts.RegisteredProjectionConsumer(
        "candidate-consumer", "projection-consumer-v1",
        (("DURABLE_ENTRY_ADMITTED", "entry-admission-v2"),), ("DURABLE_ECONOMIC_ORDER",), BUILD,
    )


def _candidate():
    event = _event()
    mapped = m.projection_mapping_contracts.map_admission_outbox_to_projection(event)
    binding = m.candidate_shadow_contracts.CandidateGenerationBinding.from_generation(SimpleNamespace(
        generation_id=str(GENERATION_ID), consumer_name="candidate-consumer", build_fingerprint=BUILD,
        source_high_watermark=7, processed_high_watermark=7, state="READY",
    ), _consumer())
    snapshot = m.shadow_diff_contracts.ShadowSourceSnapshot(
        "candidate", 7, 8, "paper-main", "BTC-USDT", "swap", "candidate-v1", NOW,
        m.shadow_diff_contracts.ShadowSourceStatus.READY,
        {"equity": m.shadow_diff_contracts.ShadowFactValue("100", m.shadow_diff_contracts.ShadowValueKind.MONETARY, "USDT")},
        generation_id=GENERATION_ID, checkpoint_watermark=7,
    )
    return event, mapped, m.candidate_shadow_contracts.CandidateProjectionGeneration(binding, (mapped,), snapshot)


def _reconciliation():
    external = m.reconciliation_contracts.ReconciliationSourceSnapshot(
        "venue", "v1", 7, 8, "paper-main", "binance", "swap", "BTC-USDT", None, NOW,
        {"position": m.reconciliation_contracts.ReconciliationFactValue("1", m.reconciliation_contracts.ReconciliationFactKind.QUANTITY, "BTC")},
    )
    local = m.reconciliation_contracts.ReconciliationSourceSnapshot(
        "local", "v1", 7, 8, "paper-main", "binance", "swap", "BTC-USDT", None, NOW,
        {"position": m.reconciliation_contracts.ReconciliationFactValue("1", m.reconciliation_contracts.ReconciliationFactKind.QUANTITY, "BTC")},
        generation_id=GENERATION_ID, checkpoint_watermark=7,
    )
    run = m.reconciliation_contracts.ReconciliationRun(
        RUN_ID, 7, 8, "paper-main", "binance", "swap", "BTC-USDT", None, GENERATION_ID,
        "candidate-consumer", BUILD, 7, "venue", "v1", external.source_fingerprint, NOW, NOW, NOW,
        "recon-corr", m.reconciliation_contracts.ReconciliationPolicySnapshot("policy-v1", False),
        m.reconciliation_contracts.ReconciliationRunState.COMPLETE,
    )
    return m.reconciliation_contracts.ReconciliationResult(run, local, external, ())


class G4BReadonlyContractTests(unittest.TestCase):
    def test_complete_chain_is_typed_deterministic_and_read_only(self):
        event, mapped, candidate = _candidate()
        request = m.projection_consumer_contracts.ProjectionConsumeRequest(_consumer(), "22222222-2222-2222-2222-222222222222", 7, event, NOW)
        consume = m.projection_consumer_contracts.ProjectionConsumeResult(request, m.projection_consumer_contracts.ConsumerApplyDisposition.CREATED, 7)
        run = m.shadow_diff_contracts.ShadowComparisonRun(
            RUN_ID, 7, 8, "paper-main", "BTC-USDT", "swap", "legacy", "v1", "b" * 64, GENERATION_ID,
            "candidate-consumer", BUILD, 7, NOW, "shadow-corr",
            m.shadow_diff_contracts.ShadowTolerancePolicy("policy-v1", "0.01", "0.10", "0.001"),
        )
        shadow = m.candidate_shadow_contracts.bind_candidate_shadow(run, candidate)
        receipt = m.g4b_readonly_contracts.validate_g4b_readonly_chain(event, consume, candidate, shadow, _reconciliation())
        self.assertTrue(receipt.is_read_only)
        self.assertEqual(receipt.chain_fingerprint, m.g4b_readonly_contracts.validate_g4b_readonly_chain(event, consume, candidate, shadow, _reconciliation()).chain_fingerprint)

    def test_chain_rejects_projection_or_event_mismatch(self):
        event, mapped, candidate = _candidate()
        request = m.projection_consumer_contracts.ProjectionConsumeRequest(_consumer(), str(GENERATION_ID), 7, event, NOW)
        consume = m.projection_consumer_contracts.ProjectionConsumeResult(request, m.projection_consumer_contracts.ConsumerApplyDisposition.REPLAYED, 7)
        run = m.shadow_diff_contracts.ShadowComparisonRun(
            RUN_ID, 7, 8, "paper-main", "BTC-USDT", "swap", "legacy", "v1", "b" * 64, GENERATION_ID,
            "candidate-consumer", BUILD, 7, NOW, "shadow-corr",
            m.shadow_diff_contracts.ShadowTolerancePolicy("policy-v1", "0.01", "0.10", "0.001"),
        )
        shadow = m.candidate_shadow_contracts.bind_candidate_shadow(run, candidate)
        with self.assertRaises(m.g4b_readonly_contracts.G4BReadonlyContractError):
            bad_projection = replace(mapped, event_id="deadbeef")
            m.g4b_readonly_contracts.G4BReadonlyChainReceipt(event, m.entry_admission_v2_contracts.parse_admission_outbox_event(event), bad_projection, consume, candidate, shadow, _reconciliation())


if __name__ == "__main__":
    unittest.main()
