"""Load PR-12 admission modules without importing Flask application startup."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

_MISSING = object()

def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None: raise RuntimeError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module); return module

def load_pr12_gateway() -> SimpleNamespace:
    app = Path(__file__).resolve().parents[1] / "app"
    names = ("app", "app.domain", "app.services", "app.domain.decimal_values", "app.domain.order_contracts", "app.domain.canonical_entry_contracts", "app.services.entry_admission_gateway")
    previous = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        package = ModuleType("app"); package.__path__ = [str(app)]
        domain = ModuleType("app.domain"); domain.__path__ = [str(app / "domain")]
        services = ModuleType("app.services"); services.__path__ = [str(app / "services")]
        sys.modules.update({"app": package, "app.domain": domain, "app.services": services})
        decimal = _load("app.domain.decimal_values", app / "domain" / "decimal_values.py")
        order = _load("app.domain.order_contracts", app / "domain" / "order_contracts.py")
        canonical = _load("app.domain.canonical_entry_contracts", app / "domain" / "canonical_entry_contracts.py")
        gateway = _load("app.services.entry_admission_gateway", app / "services" / "entry_admission_gateway.py")
        return SimpleNamespace(decimal=decimal, order=order, canonical=canonical, gateway=gateway)
    finally:
        for name in reversed(names):
            if previous[name] is _MISSING: sys.modules.pop(name, None)
            else: sys.modules[name] = previous[name]
