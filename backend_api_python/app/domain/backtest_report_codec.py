"""Canonical JSON codec for immutable deterministic backtest reports.

The legacy ``qd_backtest_runs.result_json`` column contains an intentionally
untyped payload.  This codec defines the only shape that a future read-only
adapter may accept as a :class:`BacktestReportSnapshot`: every Decimal is
encoded as a decimal string, every timestamp is explicit UTC, and the stored
fingerprint is checked after reconstruction.  It never runs a strategy or
persists anything itself.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from .backtest_dataset_contracts import BacktestDatasetSnapshot
from .backtest_metrics_contracts import (
    BacktestEquityPoint,
    BacktestMetrics,
    BacktestTradeResult,
    WalkForwardWindow,
)
from .backtest_report_contracts import (
    BACKTEST_REPORT_CONTRACT_VERSION,
    BacktestReportError,
    BacktestReportSnapshot,
)
from .deterministic_backtest_contracts import (
    BacktestBar,
    BacktestRunFacts,
)
from .market_data_quality_contracts import (
    DataQualityAssessment,
    DataQualityStatus,
    MarketDataEventFact,
)


class BacktestReportCodecError(BacktestReportError):
    """Serialized report facts are missing, non-canonical, or tampered."""


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise BacktestReportCodecError(f"{field} must be an object")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or not value.isascii():
        raise BacktestReportCodecError(f"{field} must be canonical text")
    return value


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, (float, bool)) or not isinstance(value, (str, Decimal, int)):
        raise BacktestReportCodecError(f"{field} must be a decimal string")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BacktestReportCodecError(f"{field} must be a decimal string") from exc
    if not result.is_finite():
        raise BacktestReportCodecError(f"{field} must be finite")
    return result


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise BacktestReportCodecError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BacktestReportCodecError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise BacktestReportCodecError(f"{field} must use zero-offset UTC")
    return parsed.astimezone(timezone.utc)


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _event_to_dict(event: MarketDataEventFact) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "source": event.source,
        "instrument_id": event.instrument_id,
        "occurred_at": event.occurred_at.isoformat(),
        "observed_at": event.observed_at.isoformat(),
        "sequence": event.sequence,
        "dataset_snapshot_id": event.dataset_snapshot_id,
        "rule_version": event.rule_version,
        "payload_fingerprint": event.payload_fingerprint,
    }


def _bar_to_dict(bar: BacktestBar) -> dict[str, Any]:
    return {
        "instrument_id": bar.instrument_id,
        "open_time": bar.open_time.isoformat(),
        "close_time": bar.close_time.isoformat(),
        "open_price": _decimal_text(bar.open_price),
        "high_price": _decimal_text(bar.high_price),
        "low_price": _decimal_text(bar.low_price),
        "close_price": _decimal_text(bar.close_price),
        "volume": _decimal_text(bar.volume),
        "sequence": bar.sequence,
        "snapshot_id": bar.snapshot_id,
    }


def serialize_backtest_report(report: BacktestReportSnapshot) -> dict[str, Any]:
    """Return canonical JSON-compatible facts for a typed report."""

    if not isinstance(report, BacktestReportSnapshot):
        raise BacktestReportCodecError("report must be a typed BacktestReportSnapshot")
    run = report.run
    dataset = report.dataset
    metrics = report.metrics
    quality = dataset.quality
    return {
        "contract_version": BACKTEST_REPORT_CONTRACT_VERSION,
        "run": {
            "run_id": run.run_id,
            "dataset_snapshot_id": run.dataset_snapshot_id,
            "instrument_rule_version": run.instrument_rule_version,
            "fee_policy_version": run.fee_policy_version,
            "slippage_policy_version": run.slippage_policy_version,
            "initial_cash": _decimal_text(run.initial_cash),
            "valuation_ccy": run.valuation_ccy,
            "clock_start": run.clock_start.isoformat(),
            "clock_end": run.clock_end.isoformat(),
        },
        "dataset": {
            "dataset_snapshot_id": dataset.dataset_snapshot_id,
            "venue": dataset.venue,
            "market_type": dataset.market_type,
            "instrument_id": dataset.instrument_id,
            "rule_version": dataset.rule_version,
            "bars": [_bar_to_dict(item) for item in dataset.bars],
            "quality": {
                "status": quality.status.value,
                "accepted_events": [_event_to_dict(item) for item in quality.accepted_events],
                "rejected_event_ids": list(quality.rejected_event_ids),
                "as_of": quality.as_of.isoformat(),
                "assessment_fingerprint": quality.assessment_fingerprint,
            },
            "as_of": dataset.as_of.isoformat(),
        },
        "metrics": {
            "initial_equity": _decimal_text(metrics.initial_equity),
            "final_equity": _decimal_text(metrics.final_equity),
            "total_return": _decimal_text(metrics.total_return),
            "max_drawdown": _decimal_text(metrics.max_drawdown),
            "sharpe_ratio": _decimal_text(metrics.sharpe_ratio),
            "gross_pnl": _decimal_text(metrics.gross_pnl),
            "fees": _decimal_text(metrics.fees),
            "funding": _decimal_text(metrics.funding),
            "net_pnl": _decimal_text(metrics.net_pnl),
            "win_rate": _decimal_text(metrics.win_rate),
            "profit_factor": _decimal_text(metrics.profit_factor),
        },
        "walk_forward_windows": [
            {
                "window_id": item.window_id,
                "start_at": item.start_at.isoformat(),
                "end_at": item.end_at.isoformat(),
                "out_of_sample": item.out_of_sample,
            }
            for item in report.walk_forward_windows
        ],
        "report_created_at": report.report_created_at.isoformat(),
        "report_fingerprint": report.report_fingerprint,
    }


def _event(value: object, field: str) -> MarketDataEventFact:
    item = _mapping(value, field)
    try:
        return MarketDataEventFact(
            _text(item["event_id"], f"{field}.event_id"),
            _text(item["source"], f"{field}.source"),
            _text(item["instrument_id"], f"{field}.instrument_id"),
            _utc(item["occurred_at"], f"{field}.occurred_at"),
            _utc(item["observed_at"], f"{field}.observed_at"),
            item["sequence"],
            _text(item["dataset_snapshot_id"], f"{field}.dataset_snapshot_id"),
            _text(item["rule_version"], f"{field}.rule_version"),
            _text(item["payload_fingerprint"], f"{field}.payload_fingerprint"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BacktestReportCodecError(f"{field} is invalid") from exc


def deserialize_backtest_report(value: object) -> BacktestReportSnapshot:
    """Rebuild a report and verify its persisted fingerprint exactly."""

    root = _mapping(value, "report")
    if root.get("contract_version") != BACKTEST_REPORT_CONTRACT_VERSION:
        raise BacktestReportCodecError("unsupported backtest report contract")
    try:
        run_value = _mapping(root["run"], "run")
        run = BacktestRunFacts(
            _text(run_value["run_id"], "run.run_id"),
            _text(run_value["dataset_snapshot_id"], "run.dataset_snapshot_id"),
            _text(run_value["instrument_rule_version"], "run.instrument_rule_version"),
            _text(run_value["fee_policy_version"], "run.fee_policy_version"),
            _text(run_value["slippage_policy_version"], "run.slippage_policy_version"),
            _decimal(run_value["initial_cash"], "run.initial_cash"),
            _text(run_value["valuation_ccy"], "run.valuation_ccy"),
            _utc(run_value["clock_start"], "run.clock_start"),
            _utc(run_value["clock_end"], "run.clock_end"),
        )
        dataset_value = _mapping(root["dataset"], "dataset")
        quality_value = _mapping(dataset_value["quality"], "dataset.quality")
        accepted = tuple(_event(item, "dataset.quality.accepted_events") for item in quality_value["accepted_events"])
        rejected = tuple(_text(item, "dataset.quality.rejected_event_ids") for item in quality_value["rejected_event_ids"])
        quality = DataQualityAssessment(
            DataQualityStatus(quality_value["status"]),
            accepted,
            rejected,
            _utc(quality_value["as_of"], "dataset.quality.as_of"),
            _text(quality_value["assessment_fingerprint"], "dataset.quality.assessment_fingerprint"),
        )
        bars = []
        for index, raw in enumerate(dataset_value["bars"]):
            item = _mapping(raw, f"dataset.bars[{index}]")
            bars.append(BacktestBar(
                _text(item["instrument_id"], "bar.instrument_id"),
                _utc(item["open_time"], "bar.open_time"),
                _utc(item["close_time"], "bar.close_time"),
                _decimal(item["open_price"], "bar.open_price"),
                _decimal(item["high_price"], "bar.high_price"),
                _decimal(item["low_price"], "bar.low_price"),
                _decimal(item["close_price"], "bar.close_price"),
                _decimal(item["volume"], "bar.volume"),
                item["sequence"],
                _text(item["snapshot_id"], "bar.snapshot_id"),
            ))
        dataset = BacktestDatasetSnapshot(
            _text(dataset_value["dataset_snapshot_id"], "dataset.dataset_snapshot_id"),
            _text(dataset_value["venue"], "dataset.venue").lower(),
            _text(dataset_value["market_type"], "dataset.market_type").lower(),
            _text(dataset_value["instrument_id"], "dataset.instrument_id"),
            _text(dataset_value["rule_version"], "dataset.rule_version"),
            tuple(bars),
            quality,
            _utc(dataset_value["as_of"], "dataset.as_of"),
        )
        metrics_value = _mapping(root["metrics"], "metrics")
        metrics = BacktestMetrics(
            _decimal(metrics_value["initial_equity"], "metrics.initial_equity"),
            _decimal(metrics_value["final_equity"], "metrics.final_equity"),
            _decimal(metrics_value["total_return"], "metrics.total_return"),
            _decimal(metrics_value["max_drawdown"], "metrics.max_drawdown"),
            None if metrics_value["sharpe_ratio"] is None else _decimal(metrics_value["sharpe_ratio"], "metrics.sharpe_ratio"),
            _decimal(metrics_value["gross_pnl"], "metrics.gross_pnl"),
            _decimal(metrics_value["fees"], "metrics.fees"),
            _decimal(metrics_value["funding"], "metrics.funding"),
            _decimal(metrics_value["net_pnl"], "metrics.net_pnl"),
            _decimal(metrics_value["win_rate"], "metrics.win_rate"),
            None if metrics_value["profit_factor"] is None else _decimal(metrics_value["profit_factor"], "metrics.profit_factor"),
        )
        windows = tuple(
            WalkForwardWindow(
                _text(item["window_id"], "walk_forward.window_id"),
                _utc(item["start_at"], "walk_forward.start_at"),
                _utc(item["end_at"], "walk_forward.end_at"),
                item["out_of_sample"],
            )
            for item in root["walk_forward_windows"]
        )
        report = BacktestReportSnapshot(run, dataset, metrics, windows, _utc(root["report_created_at"], "report_created_at"))
    except BacktestReportCodecError:
        raise
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise BacktestReportCodecError("backtest report facts are invalid") from exc
    if root.get("report_fingerprint") != report.report_fingerprint:
        raise BacktestReportCodecError("report fingerprint mismatch")
    return report


__all__ = [
    "BacktestReportCodecError",
    "deserialize_backtest_report",
    "serialize_backtest_report",
]
