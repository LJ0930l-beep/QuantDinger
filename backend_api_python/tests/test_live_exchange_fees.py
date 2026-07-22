from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.live_trading.adapters import LiveOrderPhaseAdapter
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.contracts import OrderIntent
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.pending_orders.fee_reconciliation import (
    fee_breakdown_snapshot,
    incremental_fees,
)


def test_fee_reconciliation_reads_saved_phase_and_only_charges_delta():
    saved = {
        "phases": {
            "fee_breakdown": {"USDT": "0.03", "BNB": "0.001"},
        }
    }
    previous = fee_breakdown_snapshot(saved)
    delta = incremental_fees(
        {"USDT": 0.05, "BNB": 0.001},
        previous,
    )

    assert previous == {"USDT": 0.03, "BNB": 0.001}
    assert delta == pytest.approx({"USDT": 0.02})


def test_adapter_preserves_multi_currency_fee_breakdown():
    adapter = LiveOrderPhaseAdapter(
        client=object(),
        exchange_id="test",
        payload={},
        exchange_config={},
    )
    with patch(
        "app.services.live_trading.adapters.wait_live_order_fill",
        return_value={
            "filled": 1,
            "avg_price": 100,
            "status": "filled",
            "fees_by_ccy": {"usdt": "0.05", "bnb": "0.001"},
        },
    ):
        fill = adapter.wait_for_fill(OrderIntent(symbol="BTC/USDT", side="buy", quantity=1))

    assert fill.fees_by_ccy == {"USDT": 0.05, "BNB": 0.001}


def test_bybit_wait_for_fill_sums_authoritative_executions():
    client = BybitClient(api_key="key", secret_key="secret", category="linear")
    order = {"orderStatus": "Filled", "cumExecQty": "2", "avgPrice": "100"}
    executions = {
        "result": {
            "list": [
                {"orderId": "o-1", "execFee": "0.03", "feeCurrency": "USDT"},
                {"orderId": "o-1", "execFee": "0.02", "feeCurrency": "USDT"},
            ]
        }
    }
    with patch.object(client, "get_order", return_value=order), patch.object(
        client, "get_executions", return_value=executions
    ) as get_executions:
        result = client.wait_for_fill(symbol="BTC/USDT", order_id="o-1", max_wait_sec=0)

    assert result["fee"] == pytest.approx(0.05)
    assert result["fee_ccy"] == "USDT"
    assert result["fees_by_ccy"] == pytest.approx({"USDT": 0.05})
    get_executions.assert_called_once()


def test_htx_spot_wait_for_fill_uses_match_results_fee():
    client = HtxClient(api_key="key", secret_key="secret", market_type="spot")
    order = {
        "state": "filled",
        "field-amount": "0.1",
        "field-cash-amount": "10",
    }
    matches = {
        "status": "ok",
        "data": [
            {"filled-fees": "0.00004", "fee-currency": "BTC"},
            {"filled-fees": "0.00006", "fee-currency": "BTC"},
        ],
    }
    with patch.object(client, "get_order", return_value=order), patch.object(
        client, "get_order_match_results", return_value=matches
    ) as get_matches:
        result = client.wait_for_fill(symbol="BTC/USDT", order_id="o-2", max_wait_sec=0)

    assert result["filled"] == pytest.approx(0.1)
    assert result["avg_price"] == pytest.approx(100)
    assert result["fee"] == pytest.approx(0.0001)
    assert result["fee_ccy"] == "BTC"
    assert result["fees_by_ccy"] == pytest.approx({"BTC": 0.0001})
    get_matches.assert_called_once()


def test_htx_swap_fee_parser_prefers_nested_trades_without_double_counting():
    raw = {
        "code": 200,
        "data": {
            "details": [
                {
                    "fee": "-0.08",
                    "fee_asset": "USDT",
                    "trades": [
                        {"trade_fee": "-0.03", "fee_asset": "USDT"},
                        {"trade_fee": "-0.05", "fee_asset": "USDT"},
                    ],
                }
            ]
        },
    }

    fees = HtxClient._match_fee_breakdown(raw, default_ccy="USDT")

    assert fees == pytest.approx({"USDT": 0.08})


@pytest.mark.parametrize(
    ("client", "response", "expected"),
    [
        (BinanceFuturesClient(api_key="key", secret_key="secret"),
         {"raw": [{"tranId": 1, "symbol": "BTCUSDT", "income": "-0.25", "asset": "USDT", "time": 10}]}, -0.25),
        (OkxClient(api_key="key", secret_key="secret", passphrase="pass"),
         {"data": [{"billId": "1", "instId": "BTC-USDT-SWAP", "balChg": "0.15", "ccy": "USDT", "ts": "10"}]}, 0.15),
        (BitgetMixClient(api_key="key", secret_key="secret", passphrase="pass"),
         {"data": {"bills": [{"billId": "1", "symbol": "BTCUSDT", "amount": "-0.35", "coin": "USDT", "cTime": "10"}]}}, -0.35),
        (BybitClient(api_key="key", secret_key="secret"),
         {"result": {"list": [{"id": "1", "symbol": "BTCUSDT", "funding": "0.45", "currency": "USDT", "transactionTime": "10"}]}}, 0.45),
        (GateUsdtFuturesClient(api_key="key", secret_key="secret"),
         [{"id": "1", "contract": "BTC_USDT", "type": "fund", "change": "-0.55", "time": 10}], -0.55),
        (HtxClient(api_key="key", secret_key="secret", market_type="swap"),
         {"status": "ok", "data": {"financial_record": [{"id": "1", "contract": "BTC-USDT", "type": 30, "amount": "0.65", "created_at": 10}]}}, 0.65),
    ],
)
def test_exchange_funding_payments_use_signed_cash_flow(client, response, expected):
    method = "_swap_private_request_raw" if isinstance(client, HtxClient) else "_signed_request"
    with patch.object(client, method, return_value=response):
        rows = client.get_funding_payments(
            symbol="BTC/USDT", start_time_ms=1, end_time_ms=100, limit=100,
        )

    assert len(rows) == 1
    assert rows[0]["amount"] == pytest.approx(expected)
    assert rows[0]["asset"] == "USDT"
