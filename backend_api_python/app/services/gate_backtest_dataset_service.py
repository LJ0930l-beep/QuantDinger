"""Build a point-in-time backtest dataset from Gate public-read evidence.

The adapter is deliberately one-way and side-effect free: it accepts an
already validated :class:`GateMarketEvidenceBundle` and produces the existing
``BacktestDatasetSnapshot`` contract.  It never opens a transport, reads a
credential, persists a snapshot, or creates an order.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.backtest_dataset_contracts import BacktestDatasetSnapshot
from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.market_data_quality_contracts import (
    MarketDataEventFact,
    assess_point_in_time,
)
from app.services.gate_market_research_service import GateMarketEvidenceBundle


GATE_BACKTEST_DATASET_SERVICE_VERSION = "gate-backtest-dataset-v1"


class GateBacktestDatasetError(ValueError):
    """Gate evidence cannot be converted into a complete dataset."""


@dataclass(frozen=True, slots=True)
class GateBacktestDatasetService:
    """Convert immutable public-read candles into a backtest dataset."""

    def build(self, evidence: GateMarketEvidenceBundle) -> BacktestDatasetSnapshot:
        if not isinstance(evidence, GateMarketEvidenceBundle):
            raise GateBacktestDatasetError("evidence must be a typed Gate bundle")
        if not evidence.candles:
            raise GateBacktestDatasetError("at least one candle is required")
        intervals = {candle.interval for candle in evidence.candles}
        if len(intervals) != 1:
            raise GateBacktestDatasetError("all candles must use one interval")

        bars = tuple(
            BacktestBar(
                instrument_id=candle.instrument_id,
                open_time=candle.open_time,
                close_time=candle.close_time,
                open_price=candle.open_price,
                high_price=candle.high_price,
                low_price=candle.low_price,
                close_price=candle.close_price,
                volume=candle.volume,
                sequence=candle.sequence,
                snapshot_id=candle.snapshot_id,
            )
            for candle in evidence.candles
        )
        quality_events = tuple(
            MarketDataEventFact(
                event_id=candle.source_event_id,
                source=f"gate.candle.{candle.interval}",
                instrument_id=candle.instrument_id,
                occurred_at=candle.occurred_at,
                observed_at=candle.observed_at,
                sequence=candle.sequence,
                dataset_snapshot_id=evidence.snapshot_id,
                rule_version=candle.rule_version,
                payload_fingerprint=candle.evidence_hash,
            )
            for candle in evidence.candles
        )
        quality = assess_point_in_time(quality_events, as_of=evidence.observed_at)
        if quality.status.value != "complete":
            raise GateBacktestDatasetError("Gate candle evidence is not a complete point-in-time set")
        return BacktestDatasetSnapshot(
            dataset_snapshot_id=evidence.snapshot_id,
            venue="gate",
            market_type=evidence.market_type.value,
            instrument_id=evidence.instrument_id,
            rule_version=evidence.rule_version,
            bars=bars,
            quality=quality,
            as_of=evidence.observed_at,
        )


__all__ = [
    "GATE_BACKTEST_DATASET_SERVICE_VERSION",
    "GateBacktestDatasetError",
    "GateBacktestDatasetService",
]
