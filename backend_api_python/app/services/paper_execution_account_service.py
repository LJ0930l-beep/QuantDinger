"""Read/recover the durable PAPER v1 account projection."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.domain.readonly_paper_account_contracts import (
    PaperOrderStatus,
    ReadonlyPaperAccountSnapshot,
    ReadonlyPaperOrderFact,
)
from app.services.paper_execution_repository import PaperExecutionRepository, PaperExecutionRepositoryError


class PaperExecutionAccountServiceError(RuntimeError):
    """Durable PAPER account facts are unavailable."""


def read_durable_paper_account(connection: Any, *, user_id: int, limit: int = 200) -> ReadonlyPaperAccountSnapshot:
    try:
        repository = PaperExecutionRepository()
        orders = repository.read_orders(connection, user_id=user_id, limit=limit)
        facts = []
        for order in orders:
            status_map = {
                "CREATED": PaperOrderStatus.SUBMITTED,
                "REPLAYED": PaperOrderStatus.SUBMITTED,
                "SUBMITTED": PaperOrderStatus.SUBMITTED,
                "PARTIALLY_FILLED": PaperOrderStatus.FILLED,
                "FILLED": PaperOrderStatus.FILLED,
                "CANCELLED": PaperOrderStatus.CANCELLED,
                "REJECTED": PaperOrderStatus.REJECTED,
            }
            fill_value = None if order.fill_price is None else order.fill_price * order.fill_quantity
            facts.append(ReadonlyPaperOrderFact(
                order_uid=order.order_id, market=order.market, symbol=order.symbol,
                side=order.side, order_type=order.order_type, quantity=order.quantity,
                limit_price=order.limit_price, fill_price=order.fill_price, fill_value=fill_value,
                status=status_map[order.status.value], note="durable-paper-v1",
                created_at=order.created_at,
            ))
        observed_at = max((fact.created_at for fact in facts), default=datetime.now(timezone.utc))
        snapshot = ReadonlyPaperAccountSnapshot(user_id, tuple(facts), observed_at)
        return snapshot
    except (PaperExecutionRepositoryError, ValueError, TypeError, KeyError) as exc:
        raise PaperExecutionAccountServiceError("durable PAPER account recovery failed") from exc


def postgres_durable_paper_account_provider(user_id: int, limit: int) -> ReadonlyPaperAccountSnapshot | None:
    from app.utils.db import get_db_connection
    with get_db_connection() as connection:
        return read_durable_paper_account(connection, user_id=user_id, limit=limit)


__all__ = ["PaperExecutionAccountServiceError", "read_durable_paper_account", "postgres_durable_paper_account_provider"]
