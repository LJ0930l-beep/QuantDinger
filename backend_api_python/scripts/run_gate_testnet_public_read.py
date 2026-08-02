#!/usr/bin/env python3
"""Run one explicit Gate TestNet *public market read* rehearsal.

The command never accepts or loads an API key/secret and has no account,
order, cancel, or fill endpoint.  Network access is opt-in via ``--network``;
without it the command exits before opening a socket.  The resulting evidence
is a read-only market snapshot suitable for the existing deterministic
research pipeline, not a trading authorization.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep this explicit rehearsal usable with the bundled runtime without
# importing the Flask application factory.  The transport and read services
# are pure domain/service modules; they do not need app startup or secrets.
app_module = types.ModuleType("app")
app_module.__path__ = [str(ROOT / "app")]
domain_module = types.ModuleType("app.domain")
domain_module.__path__ = [str(ROOT / "app" / "domain")]
services_module = types.ModuleType("app.services")
services_module.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app", app_module)
sys.modules.setdefault("app.domain", domain_module)
sys.modules.setdefault("app.services", services_module)

from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, GateReadCapabilityProfile
from app.services.gate_market_research_service import GateMarketResearchService
from app.services.gate_read_http_transport import GateReadHttpTransport, GateReadHttpTransportError
from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter
from app.services.gate_testnet_market_session_service import (
    GateTestnetMarketSessionError,
    GateTestnetMarketSessionRequest,
    GateTestnetMarketSessionService,
)


def _observed_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("observed-at must be ISO-8601 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise argparse.ArgumentTypeError("observed-at must use zero-offset UTC")
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gate TestNet public-read rehearsal")
    parser.add_argument("--instrument", default="BTC_USDT")
    parser.add_argument("--observed-at", required=True, type=_observed_at)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--rule-version", default="gate-public-read-v1")
    parser.add_argument("--network", action="store_true", help="explicitly allow one public HTTPS GET sequence")
    args = parser.parse_args(argv)

    if not args.network:
        print(json.dumps({
            "status": "BLOCKED",
            "reason": "network_not_requested",
            "network_access": False,
            "live_enabled": False,
        }, sort_keys=True))
        return 2

    profile = GateReadCapabilityProfile(
        GateEnvironment.TESTNET,
        GateMarketType.SPOT,
        credential_ref="public-read-only",
        supports_public_market_data=True,
        supports_account_reads=False,
        supports_order_reads=False,
        supports_fill_reads=False,
        writes_enabled=False,
    )
    try:
        adapter = GateReadonlyAdapter(profile, GateReadHttpTransport(profile))
        session = GateTestnetMarketSessionService(
            GateMarketResearchService(adapter, "gate-testnet-public-read", "gate-public-read-v1")
        )
        receipt = session.read(GateTestnetMarketSessionRequest(
            args.instrument,
            args.observed_at,
            args.snapshot_id,
            args.rule_version,
        ))
    except (GateReadHttpTransportError, GateTestnetMarketSessionError, ValueError) as exc:
        print(json.dumps({
            "status": "FAILED",
            "reason": str(exc),
            "network_access": True,
            "live_enabled": False,
        }, sort_keys=True))
        return 1

    print(json.dumps({
        "status": "READY",
        "session_fingerprint": receipt.session_fingerprint,
        "snapshot_id": receipt.request.snapshot_id,
        "instrument_id": receipt.request.instrument_id,
        "evidence_fingerprint": receipt.evidence.bundle_fingerprint,
        "network_access": True,
        "live_enabled": False,
        "execution_boundary": "PUBLIC_MARKET_READ_ONLY",
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
