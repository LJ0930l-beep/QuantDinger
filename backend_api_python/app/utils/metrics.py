"""Prometheus metrics endpoint for QuantDinger observability.

Exposes: order_age_seconds, unknown_order_count, outbox_lag_seconds,
projection_lag_seconds, reconciliation_mismatch_count, pnl_diff_absolute.
"""

from __future__ import annotations

import time
from typing import Dict, Any

from app.utils.logger import get_logger

logger = get_logger(__name__)

# In-memory metric store (simple; replace with prometheus_client in production)
_METRICS: Dict[str, float] = {}
_METRIC_LABELS: Dict[str, Dict[str, str]] = {}


def set_gauge(name: str, value: float, labels: Dict[str, str] | None = None) -> None:
    """Set a gauge metric value."""
    _METRICS[name] = float(value)
    if labels:
        _METRIC_LABELS[name] = dict(labels)


def inc_counter(name: str, delta: float = 1.0) -> None:
    """Increment a counter metric."""
    _METRICS[name] = _METRICS.get(name, 0.0) + float(delta)


def get_metrics() -> Dict[str, Any]:
    """Return all current metrics in Prometheus text format."""
    lines = []
    for name, value in _METRICS.items():
        labels = _METRIC_LABELS.get(name, {})
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")
    return {"metrics": lines, "count": len(lines), "timestamp": time.time()}


def record_order_age(age_seconds: float) -> None:
    """Record the age of the oldest pending order."""
    set_gauge("quantdinger_order_age_seconds", age_seconds)


def record_unknown_count(count: int) -> None:
    """Record count of orders in UNKNOWN state."""
    set_gauge("quantdinger_unknown_order_count", count)


def record_outbox_lag(lag_seconds: float) -> None:
    """Record outbox processing lag."""
    set_gauge("quantdinger_outbox_lag_seconds", lag_seconds)


def record_projection_lag(lag_seconds: float) -> None:
    """Record projection pipeline lag."""
    set_gauge("quantdinger_projection_lag_seconds", lag_seconds)


def record_reconciliation_mismatch(count: int) -> None:
    """Record number of reconciliation discrepancies."""
    set_gauge("quantdinger_reconciliation_mismatch_count", count)


def record_pnl_diff(absolute_diff: float) -> None:
    """Record absolute PnL difference between local and external."""
    set_gauge("quantdinger_pnl_diff_absolute", absolute_diff)


def record_paper_order_count(count: int) -> None:
    """Record active paper order count."""
    set_gauge("quantdinger_paper_order_count", count)
