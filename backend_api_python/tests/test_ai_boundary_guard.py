"""Regression coverage for the deterministic quant / AI boundary."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from architecture.ai_boundary_guard import compare_with_baseline, load_baseline, load_manifest, scan_ai_boundary


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "backend_api_python/architecture/ai_boundary_manifest.json"


class AIBoundaryGuardTests(unittest.TestCase):
    def test_legacy_ai_baseline_is_exact_and_cannot_expand(self):
        manifest = load_manifest(MANIFEST_PATH)
        current = scan_ai_boundary(ROOT, manifest["guarded_paths"])
        comparison = compare_with_baseline(current, load_baseline(manifest))
        self.assertTrue(comparison.passed, comparison.new_violations)
        self.assertEqual(len(current), len(manifest["legacy_ai_baseline"]))

    def test_new_provider_import_in_deterministic_trading_code_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "core.py"
            source.write_text("from app.services.llm import LLMService\n", encoding="utf-8")
            violation = scan_ai_boundary(root, ["core.py"])
            self.assertEqual(violation[0].category, "ai_provider_import")

    def test_model_output_cannot_flow_directly_into_canonical_entry_or_risk_facts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "core.py"
            source.write_text(
                "from app.services.llm import LLMService\n"
                "def unsafe():\n"
                "    output = LLMService().safe_call_llm()\n"
                "    return CanonicalEntryRequestV2(output)\n",
                encoding="utf-8",
            )
            categories = {item.category for item in scan_ai_boundary(root, ["core.py"])}
            self.assertIn("ai_provider_import", categories)
            self.assertIn("model_output_to_trading_fact", categories)

    def test_manifest_has_explicit_paths_and_version(self):
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["manifest_version"], "ai-boundary-v1")
        self.assertTrue(raw["guarded_paths"])
        self.assertTrue(all("*" not in path for path in raw["guarded_paths"]))


if __name__ == "__main__":
    unittest.main()
