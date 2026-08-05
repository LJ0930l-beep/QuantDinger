"""Caller-owned persistence for deterministic PAPER orders and fills.

The repository intentionally does not commit or rollback.  A caller may group
an order, fill, position projection and recovery checkpoint in one transaction.
It is never a Gate client and has no live-trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.domain.paper_execution_contracts import (
    PaperExecutionFill,
    PaperExecutionOrder,
    PaperExecutionOrderEvent,
    PaperExecutionStatus,
)


class PaperExecutionRepositoryError(RuntimeError):
    """Typed PAPER persistence failure."""


class PaperExecutionConflict(PaperExecutionRepositoryError):
    """An immutable PAPER identity already names different facts."""


class PaperExecutionDisposition(str, Enum):
    CREATED = "CREATED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class PaperExecutionResult:
    order_id: str
    fingerprint: str
    disposition: PaperExecutionDisposition


def _row(row: Any, index: int, key: str) -> Any:
    try:
        return row[key] if isinstance(row, dict) else row[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise PaperExecutionRepositoryError("paper persistence returned an incomplete row") from exc


class PaperExecutionRepository:
    """Persist v1 PAPER facts with deterministic replay semantics."""

    def persist_order(self, connection: Any, order: PaperExecutionOrder) -> PaperExecutionResult:
        cursor = connection.cursor()
        try:
            try:
                cursor.execute(
                    """
                    INSERT INTO qd_paper_execution_orders
                      (id, user_id, idempotency_key, request_fingerprint, order_fingerprint,
                       market, symbol, market_type, side, order_type, quantity, limit_price,
                       status, fill_quantity, fill_price, fee_amount, fee_asset, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (order.order_id, order.user_id, order.idempotency_key, order.request_fingerprint,
                     order.fingerprint, order.market, order.symbol, order.market_type, order.side,
                     order.order_type, order.quantity, order.limit_price, order.status.value,
                     order.fill_quantity, order.fill_price, order.fee_amount, order.fee_asset, order.created_at),
                )
                if getattr(cursor, "rowcount", 1) != 0:
                    return PaperExecutionResult(order.order_id, order.fingerprint, PaperExecutionDisposition.CREATED)
            except Exception as exc:
                # ON CONFLICT handles the expected idempotency/primary-key
                # race without aborting the PostgreSQL transaction.  Any
                # other driver failure is typed immediately; do not issue a
                # follow-up SELECT against an aborted transaction.
                raise PaperExecutionRepositoryError("paper order persistence failed") from exc
            # An INSERT may have lost either the idempotency arbiter or the
            # primary-key arbiter.  Read both identities under one stable lock
            # order so a same-id/different-key request is a typed conflict
            # rather than an unhelpful repository error.
            cursor.execute(
                """
                SELECT id, user_id, idempotency_key, order_fingerprint
                  FROM qd_paper_execution_orders
                 WHERE id = %s
                    OR (user_id = %s AND idempotency_key = %s)
                 ORDER BY id
                 FOR UPDATE
                """,
                (order.order_id, order.user_id, order.idempotency_key),
            )
            rows = list(cursor.fetchall() or [])
            if len(rows) != 1:
                raise PaperExecutionConflict("paper order immutable identity conflict")
            row = rows[0]
            persisted_id = str(_row(row, 0, "id"))
            persisted_user_id = int(_row(row, 1, "user_id"))
            persisted_key = str(_row(row, 2, "idempotency_key"))
            persisted_fingerprint = str(_row(row, 3, "order_fingerprint"))
            if (
                persisted_fingerprint != order.fingerprint
                or persisted_id != order.order_id
                or persisted_user_id != order.user_id
                or persisted_key != order.idempotency_key
            ):
                raise PaperExecutionConflict("paper order immutable identity conflict")
            return PaperExecutionResult(persisted_id, persisted_fingerprint, PaperExecutionDisposition.REPLAYED)
        except (PaperExecutionConflict, PaperExecutionRepositoryError):
            raise
        except Exception as exc:
            raise PaperExecutionRepositoryError("paper order persistence failed") from exc
        finally:
            cursor.close()

    def append_fill(
        self,
        connection: Any,
        fill: PaperExecutionFill,
        *,
        user_id: int | None = None,
    ) -> PaperExecutionDisposition:
        """Append one fill, optionally proving the authenticated owner.

        ``user_id`` is deliberately an optional keyword for compatibility with
        the original caller-owned repository contract.  Authenticated HTTP
        routes must provide it so an order identifier from another account can
        never be used to append a fill.  The ownership predicate is part of the
        same row lock as the overfill check.
        """
        if user_id is not None and (
            isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0
        ):
            raise PaperExecutionRepositoryError("user_id must be a positive integer")
        cursor = connection.cursor()
        try:
            if user_id is None:
                cursor.execute(
                    """
                    SELECT quantity
                      FROM qd_paper_execution_orders
                     WHERE id = %s
                     FOR UPDATE
                    """,
                    (fill.order_id,),
                )
            else:
                cursor.execute(
                    """
                    SELECT quantity
                      FROM qd_paper_execution_orders
                     WHERE id = %s AND user_id = %s
                     FOR UPDATE
                    """,
                    (fill.order_id, user_id),
                )
            order_row = cursor.fetchone()
            if order_row is None:
                raise PaperExecutionRepositoryError("paper fill references an unknown order")

            # Resolve replay identity before checking aggregate capacity.  A
            # duplicate of a previously accepted final fill must replay even
            # when the order is now full; otherwise the overfill guard would
            # turn a harmless retry into a false conflict.
            cursor.execute(
                """
                SELECT id, order_id, fill_fingerprint
                  FROM qd_paper_execution_fills
                 WHERE id = %s OR fill_fingerprint = %s
                 FOR UPDATE
                """,
                (fill.fill_id, fill.fingerprint),
            )
            existing_identity_rows = list(cursor.fetchall() or [])
            if existing_identity_rows:
                if len(existing_identity_rows) != 1:
                    raise PaperExecutionConflict("paper fill identity is ambiguous")
                existing = existing_identity_rows[0]
                persisted_id = str(_row(existing, 0, "id"))
                persisted_order_id = str(_row(existing, 1, "order_id"))
                persisted_fingerprint = str(_row(existing, 2, "fill_fingerprint"))
                if (
                    persisted_id == fill.fill_id
                    and persisted_order_id == fill.order_id
                    and persisted_fingerprint == fill.fingerprint
                ):
                    return PaperExecutionDisposition.REPLAYED
                raise PaperExecutionConflict("paper fill immutable identity conflict")

            cursor.execute("SELECT COALESCE(SUM(quantity), 0) FROM qd_paper_execution_fills WHERE order_id = %s", (fill.order_id,))
            existing_row = cursor.fetchone()
            existing_quantity = _row(existing_row, 0, "existing_quantity") if existing_row is not None else 0
            if fill.quantity + existing_quantity > _row(order_row, 0, "quantity"):
                raise PaperExecutionConflict("paper fill would overfill the PAPER order")
            cursor.execute(
                """
                INSERT INTO qd_paper_execution_fills
                  (id, order_id, quantity, price, fee_amount, fee_asset, occurred_at, fill_fingerprint)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (fill.fill_id, fill.order_id, fill.quantity, fill.price, fill.fee_amount,
                 fill.fee_asset, fill.occurred_at, fill.fingerprint),
            )
            if getattr(cursor, "rowcount", 1) == 0:
                cursor.execute("""
                    SELECT id, order_id, fill_fingerprint
                      FROM qd_paper_execution_fills
                     WHERE id = %s OR fill_fingerprint = %s
                     FOR UPDATE
                """, (fill.fill_id, fill.fingerprint))
                rows = list(cursor.fetchall() or [])
                if len(rows) != 1:
                    raise PaperExecutionConflict("paper fill immutable identity conflict")
                row = rows[0]
                if (
                    str(_row(row, 0, "id")) != fill.fill_id
                    or str(_row(row, 1, "order_id")) != fill.order_id
                    or str(_row(row, 2, "fill_fingerprint")) != fill.fingerprint
                ):
                    raise PaperExecutionConflict("paper fill immutable identity conflict")
                return PaperExecutionDisposition.REPLAYED
            return PaperExecutionDisposition.CREATED
        except (PaperExecutionConflict, PaperExecutionRepositoryError):
            raise
        except Exception as exc:
            raise PaperExecutionRepositoryError("paper fill persistence failed") from exc
        finally:
            cursor.close()

    def record_recovery_checkpoint(self, connection: Any, *, user_id: int, checkpoint_version: int,
                                   last_order_id: str | None, snapshot_fingerprint: str, status: str = "READY") -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO qd_paper_recovery_checkpoints
                  (user_id, checkpoint_version, last_order_id, snapshot_fingerprint, status)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, checkpoint_version) DO UPDATE
                    SET last_order_id = EXCLUDED.last_order_id,
                        snapshot_fingerprint = EXCLUDED.snapshot_fingerprint,
                        status = EXCLUDED.status
                """,
                (user_id, checkpoint_version, last_order_id, snapshot_fingerprint, status),
            )
        except Exception as exc:
            raise PaperExecutionRepositoryError("paper recovery checkpoint persistence failed") from exc
        finally:
            cursor.close()

    def append_order_event(
        self,
        connection: Any,
        event: PaperExecutionOrderEvent,
        *,
        user_id: int | None = None,
    ) -> PaperExecutionDisposition:
        """Append an immutable order event, optionally scoped to an owner."""
        if user_id is not None and (
            isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0
        ):
            raise PaperExecutionRepositoryError("user_id must be a positive integer")
        cursor = connection.cursor()
        try:
            if user_id is None:
                cursor.execute(
                    "SELECT id FROM qd_paper_execution_orders WHERE id = %s FOR UPDATE",
                    (event.order_id,),
                )
            else:
                cursor.execute(
                    "SELECT id FROM qd_paper_execution_orders WHERE id = %s AND user_id = %s FOR UPDATE",
                    (event.order_id, user_id),
                )
            if cursor.fetchone() is None:
                raise PaperExecutionRepositoryError("paper order event references an unknown order")
            cursor.execute("SELECT COALESCE(MAX(event_seq), 0) FROM qd_paper_execution_order_events WHERE order_id = %s", (event.order_id,))
            sequence_row = cursor.fetchone()
            next_seq = int(_row(sequence_row, 0, "next_seq") if sequence_row is not None else 0) + 1
            if event.event_seq != next_seq:
                cursor.execute(
                    "SELECT event_fingerprint FROM qd_paper_execution_order_events WHERE (id = %s OR event_fingerprint = %s) AND order_id = %s FOR UPDATE",
                    (event.event_id, event.event_fingerprint, event.order_id),
                )
                existing = cursor.fetchone()
                if existing is not None and str(_row(existing, 0, "event_fingerprint")) == event.event_fingerprint:
                    return PaperExecutionDisposition.REPLAYED
                raise PaperExecutionConflict("paper order event sequence is not contiguous")
            cursor.execute(
                """
                INSERT INTO qd_paper_execution_order_events
                  (id, order_id, event_seq, event_type, occurred_at, event_fingerprint)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (event.event_id, event.order_id, event.event_seq, event.event_type.value,
                 event.occurred_at, event.event_fingerprint),
            )
            if getattr(cursor, "rowcount", 1) != 0:
                return PaperExecutionDisposition.CREATED
            cursor.execute(
                """
                SELECT event_fingerprint FROM qd_paper_execution_order_events
                 WHERE (id = %s OR event_fingerprint = %s) AND order_id = %s
                 FOR UPDATE
                """,
                (event.event_id, event.event_fingerprint, event.order_id),
            )
            row = cursor.fetchone()
            if row is None or str(_row(row, 0, "event_fingerprint")) != event.event_fingerprint:
                raise PaperExecutionConflict("paper order event immutable identity conflict")
            return PaperExecutionDisposition.REPLAYED
        except (PaperExecutionConflict, PaperExecutionRepositoryError):
            raise
        except Exception as exc:
            raise PaperExecutionRepositoryError("paper order event persistence failed") from exc
        finally:
            cursor.close()

    def read_orders(self, connection: Any, *, user_id: int, limit: int = 200) -> list[PaperExecutionOrder]:
        """Read durable v1 orders for restart/recovery projections."""
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise PaperExecutionRepositoryError("user_id must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise PaperExecutionRepositoryError("limit must be between 1 and 500")
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT o.id, o.user_id, o.idempotency_key, o.request_fingerprint, o.market, o.symbol,
                       o.market_type, o.side, o.order_type, o.quantity, o.limit_price,
                       COALESCE(latest.event_type, o.status) AS status,
                       o.created_at,
                       COALESCE(SUM(f.quantity), 0) AS fill_quantity,
                       CASE WHEN COALESCE(SUM(f.quantity), 0) = 0 THEN NULL
                            ELSE SUM(f.quantity * f.price) / SUM(f.quantity) END AS fill_price,
                       COALESCE(SUM(f.fee_amount), o.fee_amount) AS fee_amount,
                       CASE WHEN COUNT(DISTINCT f.fee_asset) > 1 THEN 'MULTI'
                            ELSE COALESCE(MAX(f.fee_asset), o.fee_asset) END AS fee_asset
                  FROM qd_paper_execution_orders o
             LEFT JOIN qd_paper_execution_fills f ON f.order_id = o.id
             LEFT JOIN LATERAL (
                 SELECT event_type FROM qd_paper_execution_order_events
                  WHERE order_id = o.id ORDER BY event_seq DESC LIMIT 1
             ) latest ON TRUE
                 WHERE o.user_id = %s
              GROUP BY o.id, o.user_id, o.idempotency_key, o.request_fingerprint, o.market, o.symbol,
                       o.market_type, o.side, o.order_type, o.quantity, o.limit_price, o.status,
                       latest.event_type, o.created_at, o.fee_amount, o.fee_asset
                 ORDER BY o.created_at, o.id
                 LIMIT %s
                """,
                (user_id, limit),
            )
            result: list[PaperExecutionOrder] = []
            for row in cursor.fetchall() or []:
                try:
                    status_value = str(_row(row, 11, "status"))
                    fill_quantity = _row(row, 13, "fill_quantity")
                    if status_value in {"CREATED", "SUBMITTED"} and fill_quantity:
                        status_value = "FILLED" if fill_quantity >= _row(row, 9, "quantity") else "PARTIALLY_FILLED"
                    result.append(PaperExecutionOrder(
                        order_id=str(_row(row, 0, "id")), user_id=int(_row(row, 1, "user_id")),
                        idempotency_key=str(_row(row, 2, "idempotency_key")),
                        request_fingerprint=str(_row(row, 3, "request_fingerprint")),
                        market=str(_row(row, 4, "market")), symbol=str(_row(row, 5, "symbol")),
                        market_type=str(_row(row, 6, "market_type")), side=str(_row(row, 7, "side")),
                        order_type=str(_row(row, 8, "order_type")), quantity=_row(row, 9, "quantity"),
                        limit_price=_row(row, 10, "limit_price"), status=PaperExecutionStatus(status_value),
                        created_at=_row(row, 12, "created_at"), fill_quantity=_row(row, 13, "fill_quantity"),
                        fill_price=_row(row, 14, "fill_price"), fee_amount=_row(row, 15, "fee_amount"),
                        fee_asset=str(_row(row, 16, "fee_asset")),
                    ))
                except Exception as exc:
                    raise PaperExecutionRepositoryError("paper order row is invalid") from exc
            return result
        except PaperExecutionRepositoryError:
            raise
        except Exception as exc:
            raise PaperExecutionRepositoryError("paper order read failed") from exc
        finally:
            cursor.close()

    def read_latest_checkpoint(self, connection: Any, *, user_id: int) -> dict[str, Any] | None:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT checkpoint_version, last_order_id, snapshot_fingerprint, status, created_at
                  FROM qd_paper_recovery_checkpoints
                 WHERE user_id = %s
                 ORDER BY checkpoint_version DESC
                 LIMIT 1
                """,
                (user_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "checkpoint_version": int(_row(row, 0, "checkpoint_version")),
                "last_order_id": None if _row(row, 1, "last_order_id") is None else str(_row(row, 1, "last_order_id")),
                "snapshot_fingerprint": str(_row(row, 2, "snapshot_fingerprint")),
                "status": str(_row(row, 3, "status")),
                "created_at": _row(row, 4, "created_at"),
            }
        except Exception as exc:
            raise PaperExecutionRepositoryError("paper recovery checkpoint read failed") from exc
        finally:
            cursor.close()


__all__ = ["PaperExecutionRepository", "PaperExecutionRepositoryError", "PaperExecutionConflict", "PaperExecutionDisposition", "PaperExecutionResult"]
