"""Load PR-10 pure-domain contracts without application startup side effects."""

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


def load_pr10_contracts() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.hard_risk_contracts", "app.domain.risk_enforcement_contracts",
        "app.services", "app.services.risk_enforcement_repository",
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
        decimal = _load("app.domain.decimal_values", app_dir / "domain" / "decimal_values.py")
        contracts = _load("app.domain.order_contracts", app_dir / "domain" / "order_contracts.py")
        hard_risk = _load("app.domain.hard_risk_contracts", app_dir / "domain" / "hard_risk_contracts.py")
        enforcement = _load("app.domain.risk_enforcement_contracts", app_dir / "domain" / "risk_enforcement_contracts.py")
        repository = _load("app.services.risk_enforcement_repository", app_dir / "services" / "risk_enforcement_repository.py")
        return SimpleNamespace(decimal=decimal, contracts=contracts, hard_risk=hard_risk, enforcement=enforcement, repository=repository)
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
