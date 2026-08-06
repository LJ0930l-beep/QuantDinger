"""Run the deterministic Gate/Paper/Shadow product rehearsal locally.

This command never loads credentials, opens a socket, touches PostgreSQL, or
enables live trading.  It is intended as the lowest-friction smoke check for
the complete research -> candidate -> admission -> paper/testnet fixture chain.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
# The deterministic rehearsal does not need Flask or an application context.
# Keep this CLI usable with the repository's lightweight Python runtime by
# exposing the backend package path without executing app/__init__.py.
if "app" not in sys.modules:
    package = ModuleType("app")
    package.__path__ = [str(BACKEND_ROOT / "app")]
    sys.modules["app"] = package

from app.services.non_live_product_rehearsal_service import build_offline_product_rehearsal


def main() -> int:
    try:
        result = build_offline_product_rehearsal()
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "live_enabled": False, "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "READY", "live_enabled": False, "result": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
