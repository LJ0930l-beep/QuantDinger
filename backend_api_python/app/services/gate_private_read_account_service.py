"""Compose Gate private read responses into the existing typed snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.gate_vertical_read_contracts import GateAuthFacts, GatePermission
from app.domain.multi_asset_capability_contracts import AssetMarketType, CapabilityEnvironment
from app.services.gate_account_read_snapshot_service import GateAccountReadSnapshotService
from app.services.gate_private_read_client import GatePrivateReadClient, GatePrivateReadError


class GatePrivateReadAccountError(RuntimeError):
    """A private payload could not form a complete typed account snapshot."""


def _account_decimal(account: dict[str, Any], *names: str) -> Decimal:
    for name in names:
        value = account.get(name)
        if value not in (None, ""):
            if isinstance(value, (float, bool)):
                raise GatePrivateReadAccountError("Gate futures account numeric fact is invalid")
            try:
                parsed = value if isinstance(value, Decimal) else Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise GatePrivateReadAccountError("Gate futures account numeric fact is invalid") from exc
            if not parsed.is_finite() or parsed < 0:
                raise GatePrivateReadAccountError("Gate futures account numeric fact is invalid")
            return parsed
    raise GatePrivateReadAccountError("Gate futures account numeric fact is unavailable")


def _select_instrument(payload: Any, instrument_id: str | None) -> Any:
    """Keep only the requested Gate rule row and fail closed when absent."""
    if not instrument_id:
        return ()
    rows = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if not isinstance(rows, (list, tuple)):
        raise GatePrivateReadAccountError("Gate instrument payload is invalid")
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            raise GatePrivateReadAccountError("Gate instrument payload is invalid")
        identity = row.get("instrument_id", row.get("name", row.get("contract", row.get("symbol", row.get("id")))))
        if str(identity or "") == str(instrument_id):
            selected.append(row)
    if not selected:
        raise GatePrivateReadAccountError("Gate instrument rule is unavailable")
    return selected


def _observed(client: GatePrivateReadClient) -> datetime:
    try:
        return datetime.fromtimestamp(int(client.timestamp_provider()), tz=timezone.utc)
    except Exception as exc:
        raise GatePrivateReadAccountError("Gate read timestamp is invalid") from exc


class GatePrivateReadAccountService:
    def read_snapshot(
        self,
        client: GatePrivateReadClient,
        *,
        market_type: AssetMarketType,
        account_scope: str,
        credential_ref: str,
        valuation_ccy: str = "USDT",
        instrument_id: str | None = None,
        order_history: bool = False,
    ) -> Any:
        if not isinstance(client, GatePrivateReadClient):
            raise GatePrivateReadAccountError("typed Gate private client is required")
        if not isinstance(market_type, AssetMarketType):
            raise GatePrivateReadAccountError("market_type is required")
        observed = _observed(client)
        try:
            auth = GateAuthFacts(
                venue_id="gate", market_type=market_type,
                environment=client.credential.environment,
                account_scope=account_scope, credential_ref=credential_ref,
                permissions=(GatePermission.READ_ACCOUNT, GatePermission.READ_ORDER, GatePermission.READ_FILL),
                evidence_version="gate-private-read-v1", observed_at=observed,
            )
            if market_type is AssetMarketType.SPOT:
                balances = client.read_spot_accounts()
                instruments = _select_instrument(client.read_spot_instruments(), instrument_id) if instrument_id else ()
                orders = (
                    client.read_spot_order_history(currency_pair=instrument_id)
                    if instrument_id and order_history
                    else client.read_spot_orders(currency_pair=instrument_id) if instrument_id else ()
                )
                fills = client.read_spot_fills(currency_pair=instrument_id) if instrument_id else ()
                positions = ()
                account_book = ()
            else:
                raw_account = client.read_futures_accounts()
                instruments = _select_instrument(client.read_futures_instruments(), instrument_id) if instrument_id else ()
                account = raw_account.get("data", raw_account) if isinstance(raw_account, dict) else raw_account
                if not isinstance(account, dict):
                    raise GatePrivateReadAccountError("Gate futures account payload is invalid")
                available_dec = _account_decimal(account, "available", "available_balance")
                total_dec = _account_decimal(account, "total", "total_balance")
                # Unified futures accounts may legitimately return total=0
                # while exposing usable cross balance in available. Prefer an
                # explicit cross margin balance; otherwise derive the balance
                # from the documented occupied-margin components.
                if total_dec < available_dec:
                    if account.get("cross_margin_balance") not in (None, ""):
                        total_dec = _account_decimal(account, "cross_margin_balance")
                    else:
                        order_margin = _account_decimal(account, "cross_order_margin", "order_margin")
                        position_margin = _account_decimal(account, "cross_initial_margin", "position_initial_margin")
                        total_dec = available_dec + order_margin + position_margin
                if total_dec < available_dec:
                    raise GatePrivateReadAccountError("Gate futures account balance is inconsistent")
                balances = [{"asset": valuation_ccy, "total": total_dec,
                             "available": available_dec, "locked": total_dec - available_dec}]
                positions = client.read_futures_positions()
                orders = (
                    client.read_futures_order_history(contract=instrument_id)
                    if instrument_id and order_history
                    else client.read_futures_orders(contract=instrument_id) if instrument_id else ()
                )
                fills = client.read_futures_fills(contract=instrument_id) if instrument_id else ()
                account_book = client.read_futures_account_book(contract=instrument_id) if instrument_id else client.read_futures_account_book()
            return GateAccountReadSnapshotService().read_from_payloads(
                auth, balances=balances, positions=positions, orders=orders, fills=fills,
                account_book=account_book, instruments=instruments, valuation_ccy=valuation_ccy,
                observed_at=observed, rule_version="gate-private-read-instrument-v1",
            )
        except GatePrivateReadError:
            raise
        except Exception as exc:
            raise GatePrivateReadAccountError("Gate private account payload is invalid") from exc


__all__ = ["GatePrivateReadAccountError", "GatePrivateReadAccountService"]
