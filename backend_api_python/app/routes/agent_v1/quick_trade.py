"""Trading (class T) — paper-only by default, hard-gated for live execution.

Live execution from agents requires *all* of the following:
  1. Token has scope `T`.
  2. Token has `paper_only=false` (operator must flip explicitly).
  3. Server-side env `AGENT_LIVE_TRADING_ENABLED=true` (deployment kill switch).

Until live is unlocked, this endpoint records orders to `qd_agent_paper_orders`
using the latest market price as the simulated fill — so AI workflows can
exercise the round trip without ever touching exchange credentials.
"""
from __future__ import annotations

import os
import time
import uuid
import hashlib
import json
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any

from app.services.kline import KlineService
from app.domain.paper_execution_contracts import PaperExecutionOrder, PaperExecutionStatus
from app.services.paper_execution_repository import PaperExecutionRepository
from app.utils.agent_auth import (
    SCOPE_T, agent_required, current_token, current_user_id,
    instrument_allowed, market_allowed, paper_only, with_idempotency,
)
from app.utils.agent_jobs import record_completed_job
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from flask import request

from . import agent_v1_bp
from ._helpers import envelope, error, get_json_or_400

logger = get_logger(__name__)
_kline = KlineService()
_ORDER_FIELDS = {
    "market", "symbol", "side", "qty", "order_type", "limit_price",
    "credential_id", "market_type", "leverage", "margin_mode", "tp_price", "sl_price",
}


def _live_trading_kill_switch() -> bool:
    return os.getenv("AGENT_LIVE_TRADING_ENABLED", "false").lower() in ("1", "true", "yes")


def _last_price(market: str, symbol: str) -> float | None:
    try:
        rows = _kline.get_kline(market=market, symbol=symbol, timeframe="1m", limit=1) or []
        if not rows:
            return None
        last = rows[-1]
        if isinstance(last, dict):
            for k in ("close", "c", "Close"):
                v = last.get(k)
                if v is not None:
                    return float(v)
        return None
    except Exception as exc:
        logger.warning(f"agent_v1 quick_trade last_price failed: {exc}")
        return None


def _paper_fill_outcome(body: dict, last_price: float | None) -> tuple[float | None, str, str]:
    if last_price is None:
        return None, "rejected", "no last price available; recorded without fill"
    order_type = str(body.get("order_type") or "market").strip().lower()
    if order_type == "market":
        return float(last_price), "filled", ""
    side = str(body.get("side") or "").strip().lower()
    limit_price = float(body.get("limit_price") or body.get("limitPrice") or 0)
    marketable = (
        side == "buy" and float(last_price) <= limit_price
    ) or (
        side == "sell" and float(last_price) >= limit_price
    )
    if marketable:
        return float(last_price), "filled", ""
    return None, "submitted", "paper limit order is waiting for its trigger price"


def _record_paper_order(*, body: dict, fill_price: float | None, status: str, note: str = "") -> dict:
    order_uid = uuid.uuid4().hex
    market = (body.get("market") or "").strip()
    symbol = (body.get("symbol") or "").strip()
    side = (body.get("side") or "").strip().lower()
    order_type = (body.get("order_type") or body.get("orderType") or "market").strip().lower()
    qty = float(body.get("qty") or body.get("quantity") or 0)
    limit_price = body.get("limit_price") or body.get("limitPrice")
    if limit_price is not None:
        limit_price = float(limit_price)

    fill_value = (fill_price * qty) if (fill_price is not None and qty) else None

    with get_db_connection() as db:
        # Durable v1 facts are written in the caller-owned transaction first;
        # the legacy row remains a compatibility read surface during rollout.
        market_type = str(body.get("market_type") or body.get("marketType") or "spot").strip().lower()
        if market_type in {"swap", "future", "futures", "perp"}:
            market_type = "perpetual"
        if market_type not in {"spot", "perpetual"}:
            market_type = "spot"
        request_material = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        request_fingerprint = hashlib.sha256(request_material.encode("utf-8")).hexdigest()
        durable_order = PaperExecutionOrder(
            order_id=str(uuid.UUID(order_uid)),
            user_id=current_user_id(),
            idempotency_key=request.headers.get("Idempotency-Key") or f"paper-{order_uid}",
            request_fingerprint=request_fingerprint,
            market=market,
            symbol=symbol,
            market_type=market_type,
            side=side.upper(),
            order_type=order_type.upper(),
            quantity=Decimal(str(qty)),
            limit_price=None if limit_price is None else Decimal(str(limit_price)),
            status=PaperExecutionStatus.FILLED if status == "filled" else PaperExecutionStatus.SUBMITTED,
            created_at=datetime.now(timezone.utc),
            fill_quantity=Decimal(str(qty if fill_price is not None else 0)),
            fill_price=None if fill_price is None else Decimal(str(fill_price)),
            fee_amount=Decimal("0"),
            fee_asset=str(body.get("fee_asset") or "USDT").upper(),
        )
        PaperExecutionRepository().persist_order(db, durable_order)
        if fill_price is not None:
            fill_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"paper-fill:{durable_order.order_id}:{durable_order.fingerprint}"))
            from app.domain.paper_execution_contracts import PaperExecutionFill
            PaperExecutionRepository().append_fill(db, PaperExecutionFill(
                fill_id=fill_id, order_id=durable_order.order_id, quantity=Decimal(str(qty)),
                price=Decimal(str(fill_price)), fee_amount=Decimal("0"), fee_asset=durable_order.fee_asset,
                occurred_at=durable_order.created_at,
            ))
        if status != "filled":
            from app.domain.paper_execution_contracts import PaperExecutionEventType, PaperExecutionOrderEvent
            PaperExecutionRepository().append_order_event(db, PaperExecutionOrderEvent(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"paper-submitted:{durable_order.order_id}")),
                order_id=durable_order.order_id, event_seq=1,
                event_type=PaperExecutionEventType.SUBMITTED, occurred_at=durable_order.created_at,
            ))
        cur = db.cursor()
        cur.execute(
            """
            INSERT INTO qd_agent_paper_orders
              (order_uid, user_id, agent_token_id, market, symbol, side, order_type,
               qty, limit_price, fill_price, fill_value, status, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                order_uid, current_user_id(), int(current_token().get("id") or 0),
                market, symbol, side, order_type,
                qty, limit_price, fill_price, fill_value, status, note,
            ),
        )
        db.commit()
        cur.close()

    return {
        "order_uid": order_uid,
        "market": market,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "qty": qty,
        "limit_price": limit_price,
        "fill_price": fill_price,
        "fill_value": fill_value,
        "status": status,
        "paper": True,
        "note": note,
    }


def _place_live_order(*, body: dict, user_id: int) -> dict:
    raise RuntimeError("Agent quick-trade execution is permanently disabled")
    pass  # SC-15: legacy body retired


@agent_v1_bp.route("/quick-trade/orders", methods=["POST"])
@agent_required(SCOPE_T)
def place_order():
    """Place an order. Paper-only unless explicitly unlocked (see module doc)."""
    return error(
        410,
        "Agent quick-trade is permanently disabled; use deterministic non-agent workflows.",
        http=410,
    )
    pass  # SC-15: legacy body retired


@agent_v1_bp.route("/quick-trade/kill-switch", methods=["POST"])
@agent_required(SCOPE_T)
def kill_switch():
    """Cancel all of the calling tenant's open paper orders.

    This intentionally limits scope to the agent's own surface; revoking live
    exchange orders requires the human admin path (separate, audited).
    """
    return error(410, "Agent quick-trade is permanently disabled.", http=410)

    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            UPDATE qd_agent_paper_orders
            SET status = 'cancelled', note = COALESCE(note,'') || ' [kill_switch]'
            WHERE user_id = %s AND status NOT IN ('filled','cancelled','rejected')
            """,
            (current_user_id(),),
        )
        affected = cur.rowcount
        db.commit()
        cur.close()
    return envelope({"cancelled_open_paper_orders": int(affected or 0)})
