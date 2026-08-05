"""Load pure PR-12 admission modules without application startup."""

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


def load_pr12_gateway() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app",
        "app.domain",
        "app.services",
        "app.domain.decimal_values",
        "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts",
        "app.domain.canonical_entry_v2_contracts",
        "app.domain.durable_entry_persistence_contracts",
        "app.domain.hard_risk_contracts",
        "app.domain.durable_risk_enforcement_v2_contracts",
        "app.domain.outbox_projection_contracts",
        "app.domain.entry_admission_v2_contracts",
        "app.services.outbox_projection_repository",
        "app.services.entry_admission_gateway",
    )
    previous = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        services = ModuleType("app.services")
        services.__path__ = [str(app_dir / "services")]
        sys.modules.update({"app": app, "app.domain": domain, "app.services": services})
        decimal = _load("app.domain.decimal_values", app_dir / "domain" / "decimal_values.py")
        order = _load("app.domain.order_contracts", app_dir / "domain" / "order_contracts.py")
        entry = _load("app.domain.canonical_entry_contracts", app_dir / "domain" / "canonical_entry_contracts.py")
        entry_v2 = _load("app.domain.canonical_entry_v2_contracts", app_dir / "domain" / "canonical_entry_v2_contracts.py")
        durable_entry = _load("app.domain.durable_entry_persistence_contracts", app_dir / "domain" / "durable_entry_persistence_contracts.py")
        hard_risk = _load("app.domain.hard_risk_contracts", app_dir / "domain" / "hard_risk_contracts.py")
        durable_risk = _load("app.domain.durable_risk_enforcement_v2_contracts", app_dir / "domain" / "durable_risk_enforcement_v2_contracts.py")
        outbox = _load("app.domain.outbox_projection_contracts", app_dir / "domain" / "outbox_projection_contracts.py")
        admission = _load("app.domain.entry_admission_v2_contracts", app_dir / "domain" / "entry_admission_v2_contracts.py")
        outbox_repository = _load("app.services.outbox_projection_repository", app_dir / "services" / "outbox_projection_repository.py")
        gateway = _load("app.services.entry_admission_gateway", app_dir / "services" / "entry_admission_gateway.py")
        return SimpleNamespace(
            decimal=decimal,
            order=order,
            entry=entry,
            entry_v2=entry_v2,
            durable_entry=durable_entry,
            durable_risk=durable_risk,
            outbox=outbox,
            admission=admission,
            outbox_repository=outbox_repository,
            gateway=gateway,
        )
    finally:
        for name in reversed(names):
            original = previous[name]
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original
