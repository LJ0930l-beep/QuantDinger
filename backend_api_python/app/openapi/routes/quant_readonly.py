"""Read-only quant state HTTP surface.

The route is intentionally unavailable until the host application injects a
validated G4-B receipt provider.  It cannot create connections, read secrets,
call an exchange, or mutate a projection.
"""

from datetime import datetime, timezone

from flask import current_app, jsonify, request

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
from app.services.quant_operations_service import (
    QuantOperationsServiceError,
    service_from_app as quant_operations_service_from_app,
)
from app.services.non_live_run_manifest_service import (
    NonLiveRunManifestServiceError,
    service_from_app as non_live_manifest_service_from_app,
)
from app.services.deployment_readiness_service import (
    DeploymentReadinessServiceError,
    service_from_app as deployment_readiness_service_from_app,
)
from app.services.readonly_projection_summary_service import (
    ReadonlyProjectionSummaryServiceError,
    service_from_app as projection_summary_service_from_app,
)
from app.services.readonly_reconciliation_summary_service import (
    ReadonlyReconciliationSummaryServiceError,
    service_from_app as reconciliation_summary_service_from_app,
)
from app.utils.auth import get_current_user_id
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


@blp.route("/api/quant/operations/readonly", methods=["GET"])
@login_required
def get_readonly_quant_operations():
    """Return composed non-live research and release posture without writes."""

    try:
        status, body = quant_operations_service_from_app(current_app).read_response()
    except QuantOperationsServiceError:
        return jsonify({"code": 0, "msg": "operational posture unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/research-run/manifest/readonly", methods=["GET"])
@login_required
def get_readonly_non_live_run_manifest():
    """Return an injected non-live run manifest without writes or credentials."""

    try:
        status, body = non_live_manifest_service_from_app(current_app).read_response()
    except NonLiveRunManifestServiceError:
        return jsonify({"code": 0, "msg": "non-live run manifest unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/deployment/readiness/readonly", methods=["GET"])
@login_required
def get_readonly_deployment_readiness():
    """Return artifact and rollback readiness without changing deployment state."""

    try:
        status, body = deployment_readiness_service_from_app(current_app).read_response()
    except DeploymentReadinessServiceError:
        return jsonify({"code": 0, "msg": "deployment readiness unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/projection/generation/readonly", methods=["GET"])
@login_required
def get_readonly_projection_generation():
    """Return persisted projection-generation facts without claiming G4-B."""

    consumer_name = request.args.get("consumer", "candidate")
    observed_at = request.args.get("as_of")
    try:
        as_of = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if observed_at else datetime.now(timezone.utc)
        status, body = projection_summary_service_from_app(current_app).read_response(
            consumer_name=consumer_name, as_of=as_of
        )
    except (ReadonlyProjectionSummaryServiceError, ValueError, TypeError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/reconciliation/checkpoint/readonly", methods=["GET"])
@login_required
def get_readonly_reconciliation_checkpoint():
    """Return one authenticated, credential- and instrument-scoped checkpoint."""

    try:
        credential_id = int(request.args["credential_id"])
        exchange = request.args["exchange"]
        market_type = request.args["market_type"]
        account_scope = request.args["account_scope"]
        instrument_id = request.args.get("instrument_id", "")
        observed_at = request.args.get("as_of")
        as_of = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if observed_at else datetime.now(timezone.utc)
        status, body = reconciliation_summary_service_from_app(current_app).read_response(
            user_id=get_current_user_id(),
            credential_id=credential_id,
            exchange=exchange,
            market_type=market_type,
            account_scope=account_scope,
            instrument_id=instrument_id,
            as_of=as_of,
        )
    except (KeyError, ValueError, TypeError, ReadonlyReconciliationSummaryServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


__all__ = ["blp"]
