"""Load PR-11 pure contracts without importing application startup code."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


_MISSING = object()


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_pr11_contracts() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app",
        "app.domain",
        "app.domain.decimal_values",
        "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts",
        "app.domain.canonical_entry_v2_contracts",
        "app.domain.canonical_entry_adapters",
    )
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        order = _load("app.domain.order_contracts", app_dir / "domain" / "order_contracts.py")
        decimals = _load("app.domain.decimal_values", app_dir / "domain" / "decimal_values.py")
        entry = _load(
            "app.domain.canonical_entry_contracts",
            app_dir / "domain" / "canonical_entry_contracts.py",
        )
        entry_v2 = _load("app.domain.canonical_entry_v2_contracts", app_dir / "domain" / "canonical_entry_v2_contracts.py")
        adapters = _load(
            "app.domain.canonical_entry_adapters",
            app_dir / "domain" / "canonical_entry_adapters.py",
        )
        return SimpleNamespace(order=order, decimals=decimals, entry=entry, entry_v2=entry_v2, adapters=adapters)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
