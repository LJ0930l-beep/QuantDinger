"""Explicit public Gate TestNet provider for the read-only market service.

The factory is intentionally opt-in.  It creates a GET-only transport with an
opaque public-read reference; no API key or secret can enter this provider.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter
from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, GateReadCapabilityProfile
from app.services.gate_market_research_service import GateMarketResearchService
from app.services.gate_read_http_transport import GateReadHttpTransport
from app.services.gate_testnet_market_session_service import (
    GateTestnetMarketSessionRequest,
    GateTestnetMarketSessionService,
)


def provider_from_network():
    """Build a public TestNet provider; calling it performs GET-only reads."""

    sessions: dict[GateMarketType, GateTestnetMarketSessionService] = {}
    for market_type in GateMarketType:
        profile = GateReadCapabilityProfile(
            GateEnvironment.TESTNET,
            market_type,
            credential_ref="public-market-read",
            supports_public_market_data=True,
            supports_account_reads=False,
            supports_order_reads=False,
            supports_fill_reads=False,
            writes_enabled=False,
        )
        transport = GateReadHttpTransport(profile)
        adapter = GateReadonlyAdapter(profile, transport)
        sessions[market_type] = GateTestnetMarketSessionService(
            GateMarketResearchService(adapter, "gate-public", "gate-public-evidence")
        )

    def provider(
        instrument_id: str,
        market_type: GateMarketType,
        interval: str,
        candle_limit: int,
        depth_limit: int,
        observed_at: datetime,
    ):
        request = GateTestnetMarketSessionRequest(
            instrument_id=instrument_id,
            observed_at=observed_at,
            snapshot_id=f"public-{instrument_id}-{observed_at.isoformat()}",
            rule_version="gate-public-market-v1",
            interval=interval,
            candle_limit=candle_limit,
            depth_limit=depth_limit,
        )
        return sessions[market_type].read(request).evidence

    return provider


__all__ = ["provider_from_network"]
