from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
import importlib
import sys

import pytest


def _load_canonical_gate_modules():
    """Bind this module to one coherent production class graph.

    Several legacy contract tests load modules from file paths.  They restore
    ``sys.modules`` after collection, but a formatter can otherwise retain a
    class from a different module instance.  Re-importing this exact graph at
    collection time keeps the typed fill checks deterministic without touching
    production code or clearing unrelated modules.
    """

    names = (
        "app.domain.multi_asset_capability_contracts",
        "app.domain.gate_vertical_read_contracts",
        "app.domain.gate_read_formatters",
        "app.domain.gate_testnet_ledger_contracts",
        "app.domain.immutable_fill_ledger",
        "app.services.immutable_fill_ledger_repository",
        "app.services.gate_testnet_order_client",
        "app.services.gate_testnet_ledger_persistence_service",
        "app.services.gate_testnet_network_fill_settlement",
    )
    saved = {name: sys.modules.get(name) for name in names}
    for name in names:
        sys.modules.pop(name, None)
    try:
        multi = importlib.import_module(names[0])
        vertical = importlib.import_module(names[1])
        importlib.import_module(names[2])
        ledger_contracts = importlib.import_module(names[3])
        immutable_ledger = importlib.import_module(names[4])
        repository = importlib.import_module(names[5])
        order_client = importlib.import_module(names[6])
        importlib.import_module(names[7])
        settlement = importlib.import_module(names[8])
        return multi, vertical, ledger_contracts, immutable_ledger, repository, order_client, settlement
    finally:
        # Restore the canonical modules so later-collected tests keep the
        # class identities they bound at their own import time.
        for _name, _orig in saved.items():
            if _orig is None:
                sys.modules.pop(_name, None)
            else:
                sys.modules[_name] = _orig


(
    _multi,
    _vertical,
    _ledger_contracts,
    _immutable_ledger,
    _repository,
    _order_client,
    _settlement,
) = _load_canonical_gate_modules()
GateTestnetLedgerScope = _ledger_contracts.GateTestnetLedgerScope
GateOrderSide = _vertical.GateOrderSide
InstrumentAssetScope = _immutable_ledger.InstrumentAssetScope
AssetMarketType = _multi.AssetMarketType
GateTestnetNetworkSettlementError = _settlement.GateTestnetNetworkSettlementError
build_gate_testnet_settlement_scopes = _settlement.build_gate_testnet_settlement_scopes
read_and_settle_gate_testnet_order_fills_caller_owned = _settlement.read_and_settle_gate_testnet_order_fills_caller_owned
settle_gate_testnet_order_fills_caller_owned = _settlement.settle_gate_testnet_order_fills_caller_owned
GateTestnetOrderReceipt = _order_client.GateTestnetOrderReceipt
FillLedgerCommitDisposition = _repository.FillLedgerCommitDisposition
FillLedgerCommitResult = _repository.FillLedgerCommitResult
FillLedgerPersistenceScope = _repository.FillLedgerPersistenceScope


NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


class _Repo:
    def __init__(self):
        self.calls = []

    def persist_fill_bundle_caller_owned(self, connection, *, scope, fill):
        self.calls.append((connection, scope, fill))
        return FillLedgerCommitResult(
            fill_event_id=str(uuid4()),
            trade_transaction_id=str(uuid4()),
            fee_transaction_id=None,
            replay_fingerprint=fill.fill_key,
            disposition=FillLedgerCommitDisposition.APPLIED,
        )


class _Reader:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def read_spot_fills(self, *, currency_pair):
        self.calls.append(("spot", currency_pair))
        return self.payload

    def read_futures_fills(self, *, contract):
        self.calls.append(("futures", contract))
        return self.payload


def _receipt(market_type: AssetMarketType) -> GateTestnetOrderReceipt:
    return GateTestnetOrderReceipt(
        market_type=market_type,
        account_scope="gate-testnet-account",
        instrument_id="BTC_USDT",
        client_order_id="gate-v1-case-1",
        exchange_order_id="order-42",
        raw_state="filled",
        status_code=200,
        response_fingerprint="a" * 64,
    )


def _scope(order_id: str):
    now = NOW
    return (
        GateTestnetLedgerScope(
            order_id,
            InstrumentAssetScope("BTC_USDT", "BTC", "USDT"),
            "USDT",
        ),
        FillLedgerPersistenceScope(
            tenant_id=1,
            credential_id=2,
            intent_id=str(uuid4()),
            economic_order_id=order_id,
            source="REST",
            exchange_event_at=now,
            received_at=now,
            normalizer_version="gate-read-v1",
            instrument_rule_version="gate-rules-v1",
        ),
    )


@pytest.mark.parametrize("market_type", [AssetMarketType.SPOT, AssetMarketType.PERPETUAL])
def test_network_fill_settlement_uses_explicit_scope_for_spot_and_perpetual(market_type):
    receipt = _receipt(market_type)
    order_id = str(uuid4())
    scope, persistence = _scope(order_id)
    reader = _Reader([
        {
            "contract": "BTC_USDT",
            "order_id": "order-42",
            "trade_id": "trade-42",
            "side": "buy",
            "size": "0.01",
            "price": "60000",
            "fee_asset": "USDT",
            "fee": "0.60",
        }
    ])
    repo = _Repo()
    result = read_and_settle_gate_testnet_order_fills_caller_owned(
        object(), receipt,
        read_client=reader,
        observed_at=NOW,
        ledger_scope=scope,
        persistence_scope=persistence,
        repository=repo,
    )
    assert result.disposition == "APPLIED"
    assert len(repo.calls) == 1
    assert reader.calls == [("spot" if market_type is AssetMarketType.SPOT else "futures", "BTC_USDT")]
    assert repo.calls[0][0] is not None


def test_unrelated_order_fill_is_not_persisted():
    receipt = _receipt(AssetMarketType.SPOT)
    order_id = str(uuid4())
    scope, persistence = _scope(order_id)
    reader = _Reader([{
        "currency_pair": "BTC_USDT",
        "order_id": "other-order",
        "trade_id": "trade-other",
        "side": "buy",
        "size": "0.01",
        "price": "60000",
    }])
    repo = _Repo()
    result = read_and_settle_gate_testnet_order_fills_caller_owned(
        object(), receipt,
        read_client=reader,
        observed_at=NOW,
        ledger_scope=scope,
        persistence_scope=persistence,
        repository=repo,
    )
    assert result.disposition == "NO_FILL"
    assert repo.calls == []


def test_receipt_fill_scope_mismatch_fails_closed():
    receipt = _receipt(AssetMarketType.SPOT)
    order_id = str(uuid4())
    scope, persistence = _scope(order_id)
    reader = _Reader([{
        "currency_pair": "ETH_USDT",
        "order_id": "order-42",
        "trade_id": "trade-42",
        "side": "buy",
        "size": "0.01",
        "price": "3000",
    }])
    with pytest.raises(GateTestnetNetworkSettlementError):
        read_and_settle_gate_testnet_order_fills_caller_owned(
            object(), receipt,
            read_client=reader,
            observed_at=NOW,
            ledger_scope=scope,
            persistence_scope=persistence,
            repository=_Repo(),
        )


def test_settlement_does_not_commit_or_rollback():
    receipt = _receipt(AssetMarketType.PERPETUAL)
    order_id = str(uuid4())
    scope, persistence = _scope(order_id)
    repo = _Repo()
    result = settle_gate_testnet_order_fills_caller_owned(
        object(), receipt,
        ledger_scope=scope,
        persistence_scope=persistence,
        repository=repo,
    )
    assert result.disposition == "NO_FILL"


def _settlement_payload(**overrides):
    payload = {
        "market_type": "spot",
        "instrument_id": "BTC_USDT",
        "account_scope": "gate-testnet-account",
        "exchange_order_id": "order-42",
        "intent_id": str(uuid4()),
        "economic_order_id": str(uuid4()),
        "source": "REST",
        "normalizer_version": "gate-read-v1",
        "instrument_rule_version": "gate-rules-v1",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "valuation_ccy": "USDT",
        "exchange_event_at": NOW.isoformat(),
        "received_at": NOW.isoformat(),
        "observed_at": NOW.isoformat(),
    }
    payload.update(overrides)
    return payload


def test_settlement_scope_builder_requires_explicit_immutable_facts():
    result = build_gate_testnet_settlement_scopes(
        _settlement_payload(), tenant_id=1, credential_id=2
    )
    assert result.observed_at == NOW
    assert result.ledger_scope.assets.instrument_id == "BTC_USDT"
    assert result.persistence_scope.source == "REST"
    assert result.persistence_scope.credential_id == 2


def test_settlement_scope_builder_supports_durable_entry_parent_without_legacy_intent():
    result = build_gate_testnet_settlement_scopes(
        _settlement_payload(intent_id=None, durable_entry_command_id=str(uuid4())),
        tenant_id=1,
        credential_id=2,
    )
    assert result.persistence_scope.intent_id is None
    assert result.persistence_scope.durable_entry_command_id


def test_settlement_scope_builder_rejects_ambiguous_parent_identity():
    with pytest.raises(GateTestnetNetworkSettlementError):
        build_gate_testnet_settlement_scopes(
            _settlement_payload(durable_entry_command_id=str(uuid4())),
            tenant_id=1,
            credential_id=2,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_at", None),
        ("exchange_event_at", "2026-08-03T08:00:00+08:00"),
        ("received_at", "not-a-time"),
        ("quote_valuation_price", 1.5),
        ("market_type", "live"),
    ],
)
def test_settlement_scope_builder_rejects_missing_or_unsafe_facts(field, value):
    with pytest.raises(GateTestnetNetworkSettlementError):
        build_gate_testnet_settlement_scopes(
            _settlement_payload(**{field: value}), tenant_id=1, credential_id=2
        )


def test_settlement_scope_builder_keeps_cross_asset_valuation_explicit():
    result = build_gate_testnet_settlement_scopes(
        _settlement_payload(
            valuation_ccy="BTC",
            quote_valuation_price="60000",
        ),
        tenant_id=1,
        credential_id=2,
    )
    assert result.ledger_scope.valuation_ccy == "BTC"
    assert result.ledger_scope.quote_valuation_price.value == Decimal("60000")
