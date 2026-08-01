"""SC-15 retirement inventory and failure-drill contract tests."""

from __future__ import annotations

import unittest
import importlib.util
from pathlib import Path
from types import ModuleType
import sys


def _load_contracts() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "app/domain/legacy_retirement_contracts.py"
    spec = importlib.util.spec_from_file_location("legacy_retirement_contracts_test_subject", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load legacy retirement contracts")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load_contracts()


def _inventory() -> tuple[object, ...]:
    return tuple(
        m.LegacyRetirementFact(
            surface=surface,
            path=f"retired/{surface.value.lower()}.py",
            symbol="entry",
            disposition=m.LegacySurfaceDisposition.RETIRED if surface not in {m.LegacySurface.PROTECTION} else m.LegacySurfaceDisposition.ADMISSION_ONLY,
            reason="legacy surface is unreachable and cannot create order facts",
        )
        for surface in m.LegacySurface
    )


class LegacyRetirementContractTests(unittest.TestCase):
    def test_complete_inventory_is_unreachable_and_side_effect_free(self):
        facts = m.validate_legacy_retirement(_inventory())
        self.assertEqual({item.surface for item in facts}, set(m.LegacySurface))
        self.assertTrue(all(not item.reachable and not item.creates_order for item in facts))

    def test_missing_or_duplicate_surface_fails_closed(self):
        facts = _inventory()
        with self.assertRaises(m.LegacyRetirementError):
            m.validate_legacy_retirement(facts[:-1])
        with self.assertRaises(m.LegacyRetirementError):
            m.validate_legacy_retirement((*facts, facts[0]))

    def test_reachable_or_side_effect_fact_is_rejected(self):
        with self.assertRaises(m.LegacyRetirementError):
            m.LegacyRetirementFact(m.LegacySurface.GRID, "grid.py", "entry", m.LegacySurfaceDisposition.RETIRED, reachable=True, reason="bad")
        with self.assertRaises(m.LegacyRetirementError):
            m.LegacyRetirementFact(m.LegacySurface.GRID, "grid.py", "entry", m.LegacySurfaceDisposition.RETIRED, calls_exchange=True, reason="bad")

    def test_failure_drills_never_reactivate_legacy_surface(self):
        for kind in m.FailureDrillKind:
            self.assertIn(
                m.failure_drill_disposition(kind, durable_fact_exists=False),
                {m.LegacySurfaceDisposition.DISABLED, m.LegacySurfaceDisposition.ADMISSION_ONLY},
            )
        self.assertEqual(
            m.failure_drill_disposition(m.FailureDrillKind.REPLAY, durable_fact_exists=True),
            m.LegacySurfaceDisposition.ADMISSION_ONLY,
        )
        self.assertEqual(
            m.failure_drill_disposition(m.FailureDrillKind.NETWORK_FAILURE, durable_fact_exists=True),
            m.LegacySurfaceDisposition.DISABLED,
        )


if __name__ == "__main__":
    unittest.main()
