"""Load the pure domain package without importing the Flask app package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_pr01_domain() -> ModuleType:
    package_name = "pr01_domain"
    existing = sys.modules.get(package_name)
    if existing is not None:
        return existing
    package_dir = Path(__file__).resolve().parents[1] / "app" / "domain"
    spec = importlib.util.spec_from_file_location(
        package_name,
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load pure domain package")
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module
