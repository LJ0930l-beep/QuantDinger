"""Pure Gate market-read to backtest-dataset hand-off.

The hand-off consumes already formatted :class:`GateCandleFact` values.  It
does not perform HTTP I/O, read credentials, or infer missing market facts.
Every bar remains bound to the source snapshot and quality assessment so a
backtest cannot silently mix snapshots or use observations after its cutoff.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from app.domain.backtest_dataset_contracts import (
    BacktestDatasetError,
    BacktestDatasetSnapshot,
)
from app.domain.deterministic_backtest_contracts import BacktestBar
from app.domain.gate_market_read_contracts import GateCandleFact
from app.domain.market_data_quality_contracts import (
    DataQualityStatus,
    MarketDataEventFact,
    MarketDataQualityError,
    assess_point_in_time,
)


GATE_BACKTEST_DATASET_CONTRACT_VERSION = "gate-backtest-dataset-v1"


class GateBacktestDatasetError(BacktestDatasetError):
    """Gate candle facts cannot form a complete point-in-time dataset."""


def _utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise GateBacktestDatasetError(f"{field_name} must use zero-offset UTC")
    return value.astimezone(timezone.utc)


def build_gate_backtest_dataset(
    candles: Iterable[GateCandleFact],
    *,
    dataset_snapshot_id: str,
    as_of: datetime,
) -> BacktestDatasetSnapshot:
    """Build a complete immutable backtest snapshot from Gate candle facts.

    The caller must provide a tuple, making ordering explicit.  All candles
    must share instrument, market, rule version, and snapshot identity.  A
    late, duplicate, conflicting, or out-of-order event fails closed through
    the DATA-01 quality assessment rather than being silently dropped.
    """

    if not isinstance(candles, tuple):
        raise GateBacktestDatasetError("candles must be an explicit tuple")
    cutoff = _utc(as_of, "as_of")
    if not isinstance(dataset_snapshot_id, str) or not dataset_snapshot_id or dataset_snapshot_id.strip() != dataset_snapshot_id or not dataset_snapshot_id.isascii():
        raise GateBacktestDatasetError("dataset_snapshot_id must be canonical ASCII text")
    values = tuple(_canonical_candle(item) for item in candles)
    if not values:
        raise GateBacktestDatasetError("dataset requires typed Gate candle facts")
    first = values[0]
    if any(
        item.instrument_id != first.instrument_id
        or item.market_type != first.market_type
        or item.rule_version != first.rule_version
        or item.snapshot_id != dataset_snapshot_id
        for item in values
    ):
        raise GateBacktestDatasetError("Gate candles must share immutable dataset scope")
    if any(item.close_time > cutoff for item in values):
        raise GateBacktestDatasetError("dataset contains a candle after as_of")
    if any(left.sequence >= right.sequence or left.open_time >= right.open_time for left, right in zip(values, values[1:])):
        raise GateBacktestDatasetError("Gate candles must be strictly ordered")
    bars = tuple(
        BacktestBar(
            instrument_id=item.instrument_id,
            open_time=item.open_time,
            close_time=item.close_time,
            open_price=item.open_price,
            high_price=item.high_price,
            low_price=item.low_price,
            close_price=item.close_price,
            volume=item.volume,
            sequence=item.sequence,
            snapshot_id=dataset_snapshot_id,
        )
        for item in values
    )
    try:
        quality = assess_point_in_time(
            tuple(
                # The Gate evidence hash is retained as the quality payload
                # fingerprint; it is never replaced with a local timestamp.
                _quality_event(item, dataset_snapshot_id)
                for item in values
            ),
            as_of=cutoff,
        )
    except (MarketDataQualityError, ValueError) as exc:
        raise GateBacktestDatasetError("Gate candle quality assessment failed") from exc
    if quality.status is not DataQualityStatus.COMPLETE:
        raise GateBacktestDatasetError(f"Gate candle quality is {quality.status.value}")
    return BacktestDatasetSnapshot(
        dataset_snapshot_id=dataset_snapshot_id,
        venue="gate",
        market_type=first.market_type.value,
        instrument_id=first.instrument_id,
        rule_version=first.rule_version,
        bars=bars,
        quality=quality,
        as_of=cutoff,
    )


def _canonical_candle(item: object) -> GateCandleFact:
    """Rebind an equivalent typed fixture to this module's class identity.

    Some isolated contract tests load the same source file under a temporary
    module name.  Reconstructing through the canonical constructor preserves
    all validation while avoiding a false type failure caused solely by that
    test-loader detail.
    """

    if isinstance(item, GateCandleFact):
        return item
    if type(item).__name__ != "GateCandleFact":
        raise GateBacktestDatasetError("dataset requires typed Gate candle facts")
    fields = (
        "market_type", "instrument_id", "interval", "open_time", "close_time",
        "open_price", "high_price", "low_price", "close_price", "volume",
        "occurred_at", "observed_at", "sequence", "source_event_id",
        "snapshot_id", "rule_version", "evidence_hash", "venue_id", "kind",
    )
    try:
        values = {name: getattr(item, name) for name in fields}
        return GateCandleFact(**values)
    except (AttributeError, TypeError, ValueError) as exc:
        raise GateBacktestDatasetError("dataset requires complete typed Gate candle facts") from exc


def _quality_event(item: GateCandleFact, dataset_snapshot_id: str):
    return MarketDataEventFact(
        event_id=item.source_event_id,
        source="gate",
        instrument_id=item.instrument_id,
        occurred_at=item.occurred_at,
        observed_at=item.observed_at,
        sequence=item.sequence,
        dataset_snapshot_id=dataset_snapshot_id,
        rule_version=item.rule_version,
        payload_fingerprint=item.evidence_hash,
    )


__all__ = ["GATE_BACKTEST_DATASET_CONTRACT_VERSION", "GateBacktestDatasetError", "build_gate_backtest_dataset"]
