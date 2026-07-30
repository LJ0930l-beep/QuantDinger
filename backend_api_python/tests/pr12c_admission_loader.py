"""Load Canonical Entry V2 admission adapters without application startup."""

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


def load_pr12c_admission() -> SimpleNamespace:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.services",
        "app.domain.decimal_values", "app.domain.order_contracts",
        "app.domain.canonical_entry_contracts", "app.domain.canonical_entry_v2_contracts",
        "app.domain.entrypoint_v2_binding_contracts",
        "app.domain.runtime_entry_ingress_contracts",
        "app.domain.runtime_entry_resolution_contracts",
        "app.domain.runtime_entry_authority_persistence_contracts",
        "app.domain.durable_entry_persistence_contracts", "app.domain.hard_risk_contracts",
        "app.domain.durable_risk_enforcement_v2_contracts",
        "app.domain.authoritative_risk_facts_contracts",
        "app.domain.outbox_projection_contracts", "app.domain.entry_admission_v2_contracts",
        "app.domain.runtime_entry_admission_contracts",
        "app.services.durable_risk_enforcement_v2_repository",
        "app.services.durable_entry_repository",
        "app.services.outbox_projection_repository",
        "app.services.entry_admission_v2_adapters",
        "app.services.authoritative_risk_facts_provider",
        "app.services.entry_admission_gateway",
        "app.services.runtime_entry_authority_repository",
        "app.services.runtime_entry_admission_service",
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
        modules = {}
        for name, relative in (
            ("app.domain.decimal_values", "domain/decimal_values.py"),
            ("app.domain.order_contracts", "domain/order_contracts.py"),
            ("app.domain.canonical_entry_contracts", "domain/canonical_entry_contracts.py"),
            ("app.domain.canonical_entry_v2_contracts", "domain/canonical_entry_v2_contracts.py"),
            ("app.domain.entrypoint_v2_binding_contracts", "domain/entrypoint_v2_binding_contracts.py"),
            ("app.domain.runtime_entry_ingress_contracts", "domain/runtime_entry_ingress_contracts.py"),
            ("app.domain.runtime_entry_resolution_contracts", "domain/runtime_entry_resolution_contracts.py"),
            ("app.domain.runtime_entry_authority_persistence_contracts", "domain/runtime_entry_authority_persistence_contracts.py"),
            ("app.domain.durable_entry_persistence_contracts", "domain/durable_entry_persistence_contracts.py"),
            ("app.domain.hard_risk_contracts", "domain/hard_risk_contracts.py"),
            ("app.domain.durable_risk_enforcement_v2_contracts", "domain/durable_risk_enforcement_v2_contracts.py"),
            ("app.domain.authoritative_risk_facts_contracts", "domain/authoritative_risk_facts_contracts.py"),
            ("app.domain.outbox_projection_contracts", "domain/outbox_projection_contracts.py"),
            ("app.domain.entry_admission_v2_contracts", "domain/entry_admission_v2_contracts.py"),
            ("app.domain.runtime_entry_admission_contracts", "domain/runtime_entry_admission_contracts.py"),
            ("app.services.durable_entry_repository", "services/durable_entry_repository.py"),
            ("app.services.durable_risk_enforcement_v2_repository", "services/durable_risk_enforcement_v2_repository.py"),
            ("app.services.outbox_projection_repository", "services/outbox_projection_repository.py"),
            ("app.services.entry_admission_v2_adapters", "services/entry_admission_v2_adapters.py"),
            ("app.services.authoritative_risk_facts_provider", "services/authoritative_risk_facts_provider.py"),
            ("app.services.entry_admission_gateway", "services/entry_admission_gateway.py"),
            ("app.services.runtime_entry_authority_repository", "services/runtime_entry_authority_repository.py"),
            ("app.services.runtime_entry_admission_service", "services/runtime_entry_admission_service.py"),
        ):
            modules[name] = _load(name, app_dir / relative)
        return SimpleNamespace(
            decimal=modules["app.domain.decimal_values"],
            order=modules["app.domain.order_contracts"],
            entry=modules["app.domain.canonical_entry_contracts"],
            entry_v2=modules["app.domain.canonical_entry_v2_contracts"],
            entrypoint_bindings=modules["app.domain.entrypoint_v2_binding_contracts"],
            runtime_ingress=modules["app.domain.runtime_entry_ingress_contracts"],
            runtime_resolution=modules["app.domain.runtime_entry_resolution_contracts"],
            runtime_authority=modules["app.domain.runtime_entry_authority_persistence_contracts"],
            runtime_admission=modules["app.domain.runtime_entry_admission_contracts"],
            durable_entry=modules["app.domain.durable_entry_persistence_contracts"],
            hard_risk=modules["app.domain.hard_risk_contracts"],
            durable_risk=modules["app.domain.durable_risk_enforcement_v2_contracts"],
            authoritative_risk_facts=modules["app.domain.authoritative_risk_facts_contracts"],
            outbox=modules["app.domain.outbox_projection_contracts"],
            admission=modules["app.domain.entry_admission_v2_contracts"],
            durable_entry_repository=modules["app.services.durable_entry_repository"],
            risk_repository=modules["app.services.durable_risk_enforcement_v2_repository"],
            outbox_repository=modules["app.services.outbox_projection_repository"],
            adapters=modules["app.services.entry_admission_v2_adapters"],
            authoritative_risk_provider=modules["app.services.authoritative_risk_facts_provider"],
            gateway=modules["app.services.entry_admission_gateway"],
            runtime_authority_repository=modules["app.services.runtime_entry_authority_repository"],
            runtime_admission_service=modules["app.services.runtime_entry_admission_service"],
        )
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
