"""Load PR-06 pure contracts without importing the Flask application package."""

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


def load_pr06_contracts() -> SimpleNamespace:
    """Return bound pure modules and restore temporary import aliases immediately."""

    package_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app",
        "app.domain",
        "app.domain.decimal_values",
        "app.domain.venue_order_contracts",
        "app.domain.immutable_fill_ledger",
    )
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app_package = ModuleType("app")
        app_package.__path__ = [str(package_dir)]
        domain_package = ModuleType("app.domain")
        domain_package.__path__ = [str(package_dir / "domain")]
        sys.modules["app"] = app_package
        sys.modules["app.domain"] = domain_package
        decimal_values = _load("app.domain.decimal_values", package_dir / "domain" / "decimal_values.py")
        venue = _load("app.domain.venue_order_contracts", package_dir / "domain" / "venue_order_contracts.py")
        ledger = _load("app.domain.immutable_fill_ledger", package_dir / "domain" / "immutable_fill_ledger.py")
        return SimpleNamespace(decimal_values=decimal_values, venue=venue, ledger=ledger)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
