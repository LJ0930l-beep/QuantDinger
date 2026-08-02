"""Read-only quant state HTTP surface.

The route is intentionally unavailable until the host application injects a
validated G4-B receipt provider.  It cannot create connections, read secrets,
call an exchange, or mutate a projection.
"""

from flask import current_app, jsonify

from app.openapi.blueprint import HumanBlueprint as Blueprint
from app.services.readonly_quant_state_service import (
    ReadonlyQuantStateServiceError,
    service_from_app,
)
from app.services.backtest_result_service import (
    BacktestResultServiceError,
    service_from_app as backtest_service_from_app,
)
from app.services.paper_shadow_result_service import (
    PaperShadowResultServiceError,
    service_from_app as paper_shadow_service_from_app,
)
from app.services.research_readiness_service import (
    ResearchReadinessServiceError,
    service_from_app as readiness_service_from_app,
)
from app.services.strategy_catalog_service import (
    StrategyCatalogServiceError,
    service_from_app as strategy_catalog_service_from_app,
)
from app.services.research_run_result_service import (
    ResearchRunResultServiceError,
    service_from_app as research_run_service_from_app,
)
from app.services.production_readiness_service import (
    ProductionReadinessServiceError,
    service_from_app as production_readiness_service_from_app,
)
from app.services.gate_testnet_rehearsal_result_service import (
    GateTestnetRehearsalResultServiceError,
    service_from_app as gate_testnet_rehearsal_service_from_app,
)
from app.utils.auth import login_required


blp = Blueprint("quant_readonly", __name__)


@blp.route("/api/quant/readonly", methods=["GET"])
@login_required
def get_readonly_quant_state():
    """Return the validated read-only projection/shadow/reconciliation view."""

    try:
        response = service_from_app(current_app).read_response()
    except ReadonlyQuantStateServiceError:
        # Keep provider details and payloads out of the public boundary.
        return jsonify({"code": 0, "msg": "read-only state unavailable", "data": None}), 503
    return jsonify(response.body), response.http_status


@blp.route("/api/quant/backtest/readonly", methods=["GET"])
@login_required
def get_readonly_backtest_result():
    """Return an injected deterministic backtest report without side effects."""

    try:
        response = backtest_service_from_app(current_app).read_response()
    except BacktestResultServiceError:
        return jsonify({"code": 0, "msg": "backtest result unavailable", "data": None}), 503
    return jsonify(response.body), response.http_status


@blp.route("/api/quant/paper-shadow/readonly", methods=["GET"])
@login_required
def get_readonly_paper_shadow_result():
    """Return an injected Paper/Shadow run summary without side effects."""

    try:
        status, body = paper_shadow_service_from_app(current_app).read_response()
    except PaperShadowResultServiceError:
        return jsonify({"code": 0, "msg": "paper/shadow result unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/readiness", methods=["GET"])
@login_required
def get_research_readiness():
    """Return Gate/backtest/Paper readiness without side effects."""

    try:
        status, body = readiness_service_from_app(current_app).read_response()
    except ResearchReadinessServiceError:
        return jsonify({"code": 0, "msg": "research readiness unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/strategies/readonly", methods=["GET"])
@login_required
def get_readonly_strategy_catalog():
    """Return injected strategy definitions without execution authority."""

    try:
        status, body = strategy_catalog_service_from_app(current_app).read_response()
    except StrategyCatalogServiceError:
        return jsonify({"code": 0, "msg": "strategy catalog unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/research/readonly", methods=["GET"])
@login_required
def get_readonly_research_run():
    """Return an injected Gate research run without execution side effects."""

    try:
        status, body = research_run_service_from_app(current_app).read_response()
    except ResearchRunResultServiceError:
        return jsonify({"code": 0, "msg": "research run unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/release-readiness/readonly", methods=["GET"])
@login_required
def get_readonly_release_readiness():
    """Return release evidence without changing deployment state."""

    try:
        status, body = production_readiness_service_from_app(current_app).read_response()
    except ProductionReadinessServiceError:
        return jsonify({"code": 0, "msg": "release readiness unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/testnet/rehearsal/readonly", methods=["GET"])
@login_required
def get_readonly_gate_testnet_rehearsal():
    """Return injected Gate TestNet rehearsal evidence without writes."""

    try:
        status, body = gate_testnet_rehearsal_service_from_app(current_app).read_response()
    except GateTestnetRehearsalResultServiceError:
        return jsonify({"code": 0, "msg": "testnet rehearsal unavailable", "data": None}), 503
    return jsonify(body), status


__all__ = ["blp"]
