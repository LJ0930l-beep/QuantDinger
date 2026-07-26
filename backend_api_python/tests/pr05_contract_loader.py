"""Load PR-05 modules directly without importing Flask application startup."""

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


def load_pr05_contracts() -> SimpleNamespace:
    """Bind PR-05 subjects, then restore every temporary module alias."""

    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.services", "app.domain.decimal_values",
        "app.domain.order_contracts", "app.domain.venue_order_contracts",
        "app.domain.order_state_machine", "app.domain.submission_recovery_contracts",
        "app.services.order_state_repository", "app.services.submission_recovery_repository",
    )
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app_package = ModuleType("app")
        app_package.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        services = ModuleType("app.services")
        services.__path__ = [str(app_dir / "services")]
        sys.modules.update({"app": app_package, "app.domain": domain, "app.services": services})
        decimal = _load("app.domain.decimal_values", app_dir / "domain" / "decimal_values.py")
        contracts = _load("app.domain.order_contracts", app_dir / "domain" / "order_contracts.py")
        venue = _load("app.domain.venue_order_contracts", app_dir / "domain" / "venue_order_contracts.py")
        machine = _load("app.domain.order_state_machine", app_dir / "domain" / "order_state_machine.py")
        recovery = _load("app.domain.submission_recovery_contracts", app_dir / "domain" / "submission_recovery_contracts.py")
        states = _load("app.services.order_state_repository", app_dir / "services" / "order_state_repository.py")
        recovery_repo = _load("app.services.submission_recovery_repository", app_dir / "services" / "submission_recovery_repository.py")
        return SimpleNamespace(decimal=decimal, contracts=contracts, venue=venue, machine=machine,
                               recovery=recovery, states=states, recovery_repo=recovery_repo)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
