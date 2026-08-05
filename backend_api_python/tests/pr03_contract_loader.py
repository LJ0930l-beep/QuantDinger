"""Load PR-03 modules without importing the Flask application package."""

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


def load_pr03_contracts() -> SimpleNamespace:
    """Return bound PR-03 modules and restore every temporary module alias."""

    package_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.command_intent_contracts", "app.services",
        "app.services.command_intent_repository",
    )
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app_package = ModuleType("app")
        app_package.__path__ = [str(package_dir)]
        domain_package = ModuleType("app.domain")
        domain_package.__path__ = [str(package_dir / "domain")]
        services_package = ModuleType("app.services")
        services_package.__path__ = [str(package_dir / "services")]
        sys.modules["app"] = app_package
        sys.modules["app.domain"] = domain_package
        sys.modules["app.services"] = services_package
        decimal_values = _load("app.domain.decimal_values", package_dir / "domain" / "decimal_values.py")
        order_contracts = _load("app.domain.order_contracts", package_dir / "domain" / "order_contracts.py")
        contracts = _load("app.domain.command_intent_contracts", package_dir / "domain" / "command_intent_contracts.py")
        repository = _load("app.services.command_intent_repository", package_dir / "services" / "command_intent_repository.py")
        return SimpleNamespace(
            decimal_values=decimal_values,
            order_contracts=order_contracts,
            contracts=contracts,
            repository=repository,
        )
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
