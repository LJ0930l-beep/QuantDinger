import json

import pytest

from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.deployment import StrategyV2DeploymentService
from app.services.strategy_v2 import deployment


SOURCE = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@okx:swap"])
    context.subscribe(frequency="1h")
    context.set_metadata(direction_mode="both")

def handle_data(context, data):
    pass
"""


class _Cursor:
    lastrowid = 41
    rowcount = 1

    def __init__(self):
        self.params = ()

    def execute(self, _query, params=()):
        self.params = params

    def close(self):
        return None


class _Db:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        return None


class _Sources:
    @staticmethod
    def get_source(_source_id, user_id=None):
        return {"id": 9, "name": "Dual strategy", "code": SOURCE}


GATE_LEVERAGE_SOURCE = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@swap"])
    context.subscribe(frequency="1h")
    context.set_metadata(direction_mode="both")
    context.allow_leverage(max_leverage=100, min_leverage=50)

def handle_data(context, data):
    pass
"""


GATE_LEGACY_LEVERAGE_SOURCE = GATE_LEVERAGE_SOURCE.replace(
    "max_leverage=100, min_leverage=50",
    "max_leverage=20",
)


def _payload(direction_mode):
    return {
        "sourceId": 9,
        "name": "Dual strategy",
        "initialCapital": 1_000,
        "executionMode": "signal",
        "directionMode": direction_mode,
    }


def test_deployment_persists_manifest_direction_and_legacy_position_side(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    strategy_id = StrategyV2DeploymentService().save(user_id=7, payload=_payload("both"))
    trading_config = json.loads(cursor.params[-1])

    assert strategy_id == 41
    assert trading_config["direction_mode"] == "both"
    assert trading_config["position_side"] == "neutral"
    assert trading_config["strategy_manifest"]["directionMode"] == "both"


def test_deployment_rejects_direction_override_that_conflicts_with_manifest(monkeypatch):
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())

    with pytest.raises(StrategyV2ContractError, match="strategyV2.directionModeMismatch"):
        StrategyV2DeploymentService().save(user_id=7, payload=_payload("long_only"))


def test_gate_swap_accepts_only_the_current_50_to_100_contract(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: type(
        "Sources", (), {"get_source": staticmethod(lambda _source_id, user_id=None: {
            "id": 9, "name": "Gate strategy", "code": GATE_LEVERAGE_SOURCE,
        })}
    )())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))
    monkeypatch.setattr(
        StrategyV2DeploymentService,
        "_credential_exchange",
        staticmethod(lambda _user_id, _credential_id: "gate"),
    )

    payload = _payload("both")
    payload.update({
        "executionMode": "live",
        "credentialId": 12,
        "leverageEnabled": True,
        "leverage": 50,
    })
    assert StrategyV2DeploymentService().save(user_id=7, payload=payload) == 41


def test_deployment_normalizes_legacy_gate_exchange_alias(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: type(
        "Sources", (), {"get_source": staticmethod(lambda _source_id, user_id=None: {
            "id": 9, "name": "Gate alias strategy", "code": GATE_LEVERAGE_SOURCE,
        })}
    )())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))
    monkeypatch.setattr(
        StrategyV2DeploymentService,
        "_credential_exchange",
        staticmethod(lambda _user_id, _credential_id: "gateio"),
    )

    payload = _payload("both")
    payload.update({
        "executionMode": "live",
        "credentialId": 12,
        "leverageEnabled": True,
        "leverage": 50,
    })

    assert StrategyV2DeploymentService().save(user_id=7, payload=payload) == 41
    exchange_config = json.loads(cursor.params[-2])
    assert exchange_config["exchange_id"] == "gate"


def test_gate_swap_rejects_legacy_leverage_manifest_before_persisting(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: type(
        "Sources", (), {"get_source": staticmethod(lambda _source_id, user_id=None: {
            "id": 9, "name": "Legacy Gate strategy", "code": GATE_LEGACY_LEVERAGE_SOURCE,
        })}
    )())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))
    monkeypatch.setattr(
        StrategyV2DeploymentService,
        "_credential_exchange",
        staticmethod(lambda _user_id, _credential_id: "gate"),
    )

    payload = _payload("both")
    payload.update({
        "executionMode": "live",
        "credentialId": 12,
        "leverageEnabled": True,
        "leverage": 20,
    })
    with pytest.raises(StrategyV2ContractError, match="strategyV2.gateLeverageContractStale"):
        StrategyV2DeploymentService().save(user_id=7, payload=payload)
