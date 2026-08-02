"""Deterministic, fixture-only end-to-end product rehearsal.

This service is the safe integration seam for the non-live product.  It
composes Gate public market evidence, the strategy factory, portfolio sizing,
Paper/Shadow simulation, deterministic next-open backtesting, and a derived
Paper position view.  It intentionally has no credential, database, worker,
exchange-write, or LIVE capability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.deterministic_backtest_contracts import BacktestExecutionKind, BacktestRunFacts
from app.domain.gate_read_transport_contracts import GatePublicReadEndpoint, GateReadResponse
from app.domain.gate_readonly_adapter_contracts import GateReadonlyAdapter
from app.domain.gate_readonly_contracts import GateEnvironment, GateMarketType, GateReadCapabilityProfile
from app.domain.paper_shadow_contracts import PaperShadowRunFacts, SimulationMode
from app.domain.portfolio_risk_contracts import PositionSizingRequest
from app.domain.readonly_paper_account_contracts import (
    PaperOrderStatus,
    ReadonlyPaperAccountSnapshot,
    ReadonlyPaperOrderFact,
)
from app.domain.strategy_library_contracts import StrategyDefinition, StrategyFamily, StrategyParameterFact
from app.services.builtin_strategy_catalog import builtin_strategy_catalog
from app.services.gate_market_research_service import GateMarketResearchService
from app.services.gate_non_live_research_orchestrator import GateNonLiveResearchOrchestrator
from app.services.gate_testnet_market_session_service import (
    GateTestnetMarketSessionRequest,
    GateTestnetMarketSessionService,
)


UTC = timezone.utc
NON_LIVE_PRODUCT_REHEARSAL_VERSION = "non-live-product-rehearsal-v1"
_OBSERVED_AT = datetime(2026, 1, 1, 0, 8, tzinfo=UTC)


class NonLiveProductRehearsalError(ValueError):
    """Fixture evidence could not form a safe complete product rehearsal."""


def _fixture_transport(request: Any) -> GateReadResponse:
    if request.endpoint is GatePublicReadEndpoint.CANDLESTICKS:
        return GateReadResponse(200, [
            [1767225600, "200", "101", "102", "99", "100", "2", True],
            [1767225660, "202", "102", "103", "99", "101", "2", True],
            [1767225720, "204", "103", "104", "100", "102", "2", True],
            [1767225780, "206", "104", "105", "101", "103", "2", True],
            [1767225840, "208", "105", "106", "102", "104", "2", True],
            [1767225900, "220", "115", "116", "100", "105", "2", True],
            [1767225960, "222", "116", "117", "113", "115", "2", True],
            [1767226020, "224", "117", "118", "114", "116", "2", True],
        ])
    return GateReadResponse(200, {
        "id": 7,
        "current": 1767225900000,
        "update": 1767225899000,
        "bids": [["114", "1"]],
        "asks": [["115", "2"]],
    })


def _paper_snapshot(result: Any) -> ReadonlyPaperAccountSnapshot:
    intents = {item.order_id: item for item in result.deterministic_backtest.orders}
    orders = []
    for decision in result.deterministic_backtest.trace.decisions:
        if decision.decision.value != "executed":
            continue
        intent = intents[decision.order_id]
        orders.append(ReadonlyPaperOrderFact(
            order_uid=decision.order_id,
            market="paper",
            symbol=intent.instrument_id,
            side=intent.side.value,
            order_type=intent.execution_kind.value,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            fill_price=decision.fill_price,
            fill_value=intent.quantity * (decision.fill_price or Decimal("0")),
            status=PaperOrderStatus.FILLED,
            note="offline-product-rehearsal",
            created_at=decision.fill_time or intent.submitted_at,
        ))
    return ReadonlyPaperAccountSnapshot(1, tuple(orders), _OBSERVED_AT)


def build_offline_product_rehearsal() -> dict[str, object]:
    """Run one complete deterministic fixture rehearsal and return JSON-safe facts."""

    profile = GateReadCapabilityProfile(GateEnvironment.TESTNET, GateMarketType.SPOT, credential_ref="offline-fixture")
    adapter = GateReadonlyAdapter(profile, _fixture_transport)
    session_service = GateTestnetMarketSessionService(
        GateMarketResearchService(adapter, "fixture", "fixture-evidence")
    )
    request = GateTestnetMarketSessionRequest(
        "BTC_USDT", _OBSERVED_AT, "product-smoke-dataset", "gate-rules-v1"
    )
    strategy = StrategyDefinition(
        "ict-liquidity-displacement", "ict-v1", StrategyFamily.ICT, "ict-schema-v1", "gate-ohlcv-pit-v1",
        (StrategyParameterFact("lookback", "3"), StrategyParameterFact("multiplier", "1.5")),
    )
    sizing = PositionSizingRequest(
        "product-smoke-sizing", "BTC_USDT", Decimal("100"), Decimal("1"), Decimal("1000"),
        Decimal("20000"), Decimal("2"), Decimal("0.5"), _OBSERVED_AT,
    )
    paper_run = PaperShadowRunFacts(
        "product-smoke-paper", SimulationMode.PAPER, "product-smoke-dataset", strategy.strategy_id,
        "product-smoke-risk", "product-smoke-tolerance", _OBSERVED_AT,
    )
    backtest_run = BacktestRunFacts(
        "product-smoke-backtest", "product-smoke-dataset", "gate-rules-v1", "fee-v1", "slippage-v1",
        Decimal("10000"), "USDT", datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 9, tzinfo=UTC),
    )
    try:
        result = GateNonLiveResearchOrchestrator(session_service).run(
            request, strategy, sizing, paper_run, backtest_run,
            signal_id="product-smoke-signal", request_fingerprint=sizing.request_fingerprint,
            decided_at=_OBSERVED_AT, order_quantity=Decimal("1"), execution_kind=BacktestExecutionKind.MARKET,
        )
        paper = _paper_snapshot(result)
        return {
            "contract_version": NON_LIVE_PRODUCT_REHEARSAL_VERSION,
            "environment": {"PAPER": True, "SHADOW": True, "TESTNET": True, "CANARY": False, "LIVE": False},
            "market": {
                "venue": "gate",
                "instrument_id": result.dataset.instrument_id,
                "dataset_fingerprint": result.dataset.dataset_fingerprint,
                "bar_count": len(result.dataset.bars),
            },
            "strategy_catalog": [
                {"strategy_id": item.strategy_id, "version": item.version, "family": item.family.value}
                for item in builtin_strategy_catalog()
            ],
            "research": result.to_public_dict(),
            "deterministic_backtest": result.deterministic_backtest.to_public_dict(),
            "paper_account": paper.to_public_dict(),
            "execution_boundary": "READ_ONLY_FIXTURE",
            "network_access": False,
            "live_enabled": False,
        }
    except Exception as exc:
        raise NonLiveProductRehearsalError("non-live product rehearsal failed closed") from exc


__all__ = [
    "NON_LIVE_PRODUCT_REHEARSAL_VERSION",
    "NonLiveProductRehearsalError",
    "build_offline_product_rehearsal",
]
