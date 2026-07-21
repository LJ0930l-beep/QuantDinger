from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from architecture.order_side_effect_guard import (
    DEFAULT_PROTECTED_PATHS,
    compare_with_baseline,
    load_baseline,
    scan_order_side_effects,
)


class OrderArchitectureGuardTests(unittest.TestCase):
    def _write(self, root: Path, relative_path: str, source: str) -> Path:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def test_repository_legacy_baseline_is_exact(self):
        repo_root = Path(__file__).resolve().parents[2]
        baseline = load_baseline(
            repo_root / "backend_api_python" / "architecture" / "order_side_effect_baseline.json"
        )
        current = scan_order_side_effects(repo_root)
        comparison = compare_with_baseline(current, baseline)
        self.assertTrue(comparison.passed)
        self.assertGreater(len(current), 0)

    def test_agent_mcp_grid_and_human_direct_calls_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(
                root,
                "backend_api_python/app/routes/quick_trade.py",
                "def human(exchange):\n    return exchange.submit_order({})\n",
            )
            self._write(
                root,
                "backend_api_python/app/routes/agent_v1/quick_trade.py",
                "from live import place_order_from_signal as send\n"
                "def agent():\n    return send('buy')\n",
            )
            self._write(
                root,
                "backend_api_python/app/services/grid/engine.py",
                "class Grid:\n    def run(self):\n        return self.exchange.client.place_market_order()\n",
            )
            self._write(
                root,
                "mcp_server/src/quantdinger_mcp/server.py",
                "def mcp(exchange):\n    return getattr(exchange, 'cancel_order')('1')\n",
            )
            violations = scan_order_side_effects(root, DEFAULT_PROTECTED_PATHS)
            self.assertEqual(len(violations), 4)
            self.assertEqual(
                {item.symbol for item in violations},
                {"human", "agent", "Grid.run", "mcp"},
            )

    def test_alias_and_attribute_chain_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(
                root,
                "protected.py",
                "def f(client, service):\n"
                "    send = client.place_order\n"
                "    send({})\n"
                "    service.exchange.adapter.cancel_order('1')\n",
            )
            violations = scan_order_side_effects(root, ["protected.py"])
            self.assertEqual(len(violations), 2)
            self.assertTrue(any(item.pattern.startswith("alias:send->") for item in violations))
            self.assertTrue(any(item.pattern.endswith("cancel_order") for item in violations))

    def test_gateway_level_submit_is_compliant(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write(
                root,
                "backend_api_python/app/routes/quick_trade.py",
                "def place(command, gateway):\n    return gateway.submit(command)\n",
            )
            self.assertEqual(scan_order_side_effects(root), ())

    def test_baseline_does_not_swallow_new_call_in_same_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = self._write(
                root,
                "backend_api_python/app/routes/quick_trade.py",
                "def place(client):\n    return client.place_order({})\n",
            )
            baseline = scan_order_side_effects(root)
            self.assertTrue(compare_with_baseline(baseline, baseline).passed)
            path.write_text(
                "def place(client):\n"
                "    client.place_order({})\n"
                "    return client.cancel_order('1')\n",
                encoding="utf-8",
            )
            comparison = compare_with_baseline(scan_order_side_effects(root), baseline)
            self.assertFalse(comparison.passed)
            self.assertEqual(len(comparison.new_violations), 1)
            self.assertEqual(comparison.new_violations[0].pattern, "client.cancel_order")

    def test_virtualenv_build_generated_and_fixture_trees_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for part in ("venv", ".venv", "build", "dist", "generated"):
                self._write(
                    root,
                    f"backend_api_python/app/routes/{part}/bad.py",
                    "exchange.place_order({})\n",
                )
            self.assertEqual(scan_order_side_effects(root), ())


if __name__ == "__main__":
    unittest.main()
