"""Load PR-08 pure shadow-diff contracts without application startup side effects."""

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


def load_pr08_contracts() -> ModuleType:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = ("app", "app.domain", "app.domain.decimal_values", "app.domain.shadow_diff_contracts")
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        _load("app.domain.decimal_values", app_dir / "domain" / "decimal_values.py")
        return _load("app.domain.shadow_diff_contracts", app_dir / "domain" / "shadow_diff_contracts.py")
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_pr08_repository() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.services", "app.domain.decimal_values",
        "app.domain.shadow_diff_contracts", "app.services.shadow_diff_repository",
    )
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        services = ModuleType("app.services")
        services.__path__ = [str(app_dir / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        _load("app.domain.decimal_values", app_dir / "domain" / "decimal_values.py")
        contracts = _load("app.domain.shadow_diff_contracts", app_dir / "domain" / "shadow_diff_contracts.py")
        repository = _load("app.services.shadow_diff_repository", app_dir / "services" / "shadow_diff_repository.py")
        return SimpleNamespace(contracts=contracts, repository=repository)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
