"""Load PR-07 pure contracts without importing Flask application startup."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


_MISSING = object()


def load_outbox_projection_contracts() -> ModuleType:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = ("app", "app.domain", "app.domain.outbox_projection_contracts")
    original = {name: sys.modules.get(name, _MISSING) for name in names}
    try:
        app = ModuleType("app")
        app.__path__ = [str(app_dir)]
        domain = ModuleType("app.domain")
        domain.__path__ = [str(app_dir / "domain")]
        sys.modules.update({"app": app, "app.domain": domain})
        spec = importlib.util.spec_from_file_location("app.domain.outbox_projection_contracts", app_dir / "domain" / "outbox_projection_contracts.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load outbox projection contracts")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_outbox_projection_repository() -> ModuleType:
    """Load PR-07 repository with its pure domain dependency only."""

    app_dir = Path(__file__).resolve().parents[1] / "app"
    names = (
        "app", "app.domain", "app.services",
        "app.domain.outbox_projection_contracts",
        "app.services.outbox_projection_repository",
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
        for name, path in (
            ("app.domain.outbox_projection_contracts", app_dir / "domain" / "outbox_projection_contracts.py"),
            ("app.services.outbox_projection_repository", app_dir / "services" / "outbox_projection_repository.py"),
        ):
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load {name}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules["app.services.outbox_projection_repository"]
    finally:
        for name in reversed(names):
            previous = original[name]
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
