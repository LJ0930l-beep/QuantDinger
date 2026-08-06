#!/usr/bin/env python3
"""Print the complete offline product rehearsal as JSON.

The implementation lives in ``app.services.non_live_product_rehearsal_service``
so the same safe integration seam can be exercised by tests and future
read-only surfaces without duplicating orchestration logic.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app_module = types.ModuleType("app"); app_module.__path__ = [str(ROOT / "app")]
domain_module = types.ModuleType("app.domain"); domain_module.__path__ = [str(ROOT / "app" / "domain")]
services_module = types.ModuleType("app.services"); services_module.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app", app_module)
sys.modules.setdefault("app.domain", domain_module)
sys.modules.setdefault("app.services", services_module)

from app.services.non_live_product_rehearsal_service import build_offline_product_rehearsal  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(build_offline_product_rehearsal(), sort_keys=True, ensure_ascii=True, indent=2))
