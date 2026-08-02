#!/usr/bin/env python3
"""Run the complete Gate -> backtest -> risk -> Paper/Shadow path locally.

This is an offline fixture rehearsal.  It deliberately uses a caller-owned
transport with deterministic market payloads, never reads credentials, never
opens a network connection, and cannot submit or cancel an order.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep this local smoke command usable with the bundled runtime even when the
# full Flask application dependencies are intentionally not installed.  The
# imported modules are pure domain/services and do not execute app startup.
app_module = types.ModuleType("app")
app_module.__path__ = [str(ROOT / "app")]
domain_module = types.ModuleType("app.domain")
domain_module.__path__ = [str(ROOT / "app" / "domain")]
services_module = types.ModuleType("app.services")
services_module.__path__ = [str(ROOT / "app" / "services")]
sys.modules.setdefault("app", app_module)
sys.modules.setdefault("app.domain", domain_module)
sys.modules.setdefault("app.services", services_module)

from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter
from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, GateReadCapabilityProfile
from app.domain.gate_read_transport_contracts import GatePublicReadEndpoint, GateReadResponse
from app.domain.paper_shadow_contracts import PaperShadowRunFacts, SimulationMode
from app.domain.portfolio_risk_contracts import PositionSizingRequest
from app.domain.strategy_library_contracts import StrategyDefinition, StrategyFamily, StrategyParameterFact
from app.services.gate_market_research_service import GateMarketResearchService
from app.services.gate_research_run_service import GateResearchRunService
from app.services.gate_testnet_market_session_service import GateTestnetMarketSessionRequest, GateTestnetMarketSessionService


UTC = timezone.utc
OBSERVED_AT = datetime(2026, 1, 1, 0, 4, tzinfo=UTC)


def fixture_transport(request):
    """Return deterministic public-read payloads; no HTTP is performed."""

    if request.endpoint is GatePublicReadEndpoint.CANDLESTICKS:
        return GateReadResponse(200, [
            [1767225600, "200", "101", "102", "99", "100", "2", True],
            [1767225660, "202", "102", "103", "99", "101", "2", True],
            [1767225720, "204", "103", "104", "100", "102", "2", True],
            [1767225780, "206", "104", "105", "101", "103", "2", True],
        ])
    return GateReadResponse(200, {"id": 7, "current": 1767225720000, "update": 1767225719000,
                                 "bids": [["100", "1"]], "asks": [["101", "2"]]})


def main() -> int:
    profile = GateReadCapabilityProfile(GateEnvironment.TESTNET, GateMarketType.SPOT, credential_ref="fixture-only")
    adapter = GateReadonlyAdapter(profile, fixture_transport)
    session = GateTestnetMarketSessionService(GateMarketResearchService(adapter, "fixture", "fixture-evidence"))
    request = GateTestnetMarketSessionRequest("BTC_USDT", OBSERVED_AT, "smoke-dataset-1", "rules-v1")
    strategy = StrategyDefinition(
        "smoke-strategy", "v1", StrategyFamily.SMC, "smoke-schema", "smoke-data",
        (StrategyParameterFact("lookback", "3"),),
    )
    sizing = PositionSizingRequest(
        "smoke-request", "BTC_USDT", Decimal("100"), Decimal("1"), Decimal("1000"),
        Decimal("20000"), Decimal("2"), Decimal("0.5"), OBSERVED_AT,
    )
    run = PaperShadowRunFacts(
        "smoke-run", SimulationMode.SHADOW, "smoke-dataset-1", "strategy-smoke",
        "risk-smoke", "tolerance-smoke", OBSERVED_AT,
    )
    result = GateResearchRunService(session).execute(
        request, strategy, sizing, run, signal_id="smoke-signal",
        request_fingerprint="smoke-request", decided_at=OBSERVED_AT,
    )
    output = result.to_public_dict()
    output["execution_boundary"] = "READ_ONLY_FIXTURE"
    output["network_access"] = False
    output["live_enabled"] = False
    print(json.dumps(output, sort_keys=True, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
