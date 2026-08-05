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
from app.services.readonly_backtest_report_service import (
    ReadonlyBacktestReportServiceError,
    service_from_app as readonly_backtest_report_service_from_app,
)
from app.services.paper_shadow_result_service import (
    PaperShadowResultServiceError,
    service_from_app as paper_shadow_service_from_app,
)
from app.services.readonly_paper_account_service import (
    ReadonlyPaperAccountServiceError,
    service_from_app as paper_account_service_from_app,
)
from app.services.readonly_paper_recovery_service import (
    ReadonlyPaperRecoveryServiceError,
    service_from_app as paper_recovery_service_from_app,
)
from app.services.paper_execution_account_service import (
    PaperExecutionAccountServiceError,
    postgres_durable_paper_account_provider,
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
from app.services.gate_testnet_execution_rehearsal_service import (
    GateTestnetExecutionRehearsalServiceError,
    service_from_app as gate_testnet_execution_service_from_app,
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
from app.services.readonly_shadow_summary_service import (
    ReadonlyShadowSummaryServiceError,
    service_from_app as shadow_summary_service_from_app,
)
from app.services.readonly_gate_account_service import (
    ReadonlyGateAccountServiceError,
    service_from_app as gate_account_service_from_app,
)
from app.services.readonly_gate_unified_account_service import (
    ReadonlyGateUnifiedAccountServiceError,
    service_from_app as gate_unified_account_service_from_app,
)
from app.services.gate_public_market_service import (
    GatePublicMarketServiceError,
    service_from_app as gate_public_market_service_from_app,
)
from app.services.readonly_gate_unified_market_service import (
    ReadonlyGateUnifiedMarketServiceError,
    service_from_app as gate_unified_market_service_from_app,
)
from app.services.gate_testnet_env_read_service import (
    GateTestnetEnvReadError,
    read_gate_testnet_environment_snapshot,
)
from app.services.non_live_product_rehearsal_service import (
    NonLiveProductRehearsalError,
    build_offline_product_rehearsal,
)
from app.services.runtime_entry_admission_http_service import (
    RuntimeEntryAdmissionApiError,
    admit_runtime_entry_payload,
    result_to_public_dict,
)
from app.domain.gate_readonly_contracts import GateMarketType
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


@blp.route("/api/quant/backtest/report/readonly", methods=["GET"])
@login_required
def get_readonly_persisted_backtest_report():
    """Return one authenticated canonical persisted backtest report."""

    try:
        run_id = int(request.args["run_id"])
        status, body = readonly_backtest_report_service_from_app(current_app).read_response(
            user_id=get_current_user_id(), run_id=run_id,
        )
    except (KeyError, ValueError, TypeError, ReadonlyBacktestReportServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/paper-shadow/readonly", methods=["GET"])
@login_required
def get_readonly_paper_shadow_result():
    """Return an injected Paper/Shadow run summary without side effects."""

    try:
        status, body = paper_shadow_service_from_app(current_app).read_response()
    except PaperShadowResultServiceError:
        return jsonify({"code": 0, "msg": "paper/shadow result unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/paper/account/readonly", methods=["GET"])
@login_required
def get_readonly_paper_account():
    """Return persisted PAPER order facts without exchange access or writes."""

    try:
        limit = request.args.get("limit", default=200, type=int)
        status, body = paper_account_service_from_app(current_app).read_response(
            user_id=int(get_current_user_id()), limit=limit
        )
    except (ReadonlyPaperAccountServiceError, TypeError, ValueError):
        return jsonify({"code": 0, "msg": "paper account unavailable", "data": None}), 503
    return jsonify(body), status


@blp.route("/api/quant/paper/recovery/readonly", methods=["GET"])
@login_required
def get_readonly_paper_recovery():
    """Replay persisted Paper facts and compare an optional checkpoint."""

    try:
        limit = request.args.get("limit", default=200, type=int)
        expected = request.args.get("expected_snapshot_fingerprint")
        status, body = paper_recovery_service_from_app(current_app).read_response(
            user_id=int(get_current_user_id()),
            limit=limit,
            expected_snapshot_fingerprint=expected,
        )
    except (ReadonlyPaperRecoveryServiceError, TypeError, ValueError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/paper/v2/account/readonly", methods=["GET"])
@login_required
def get_readonly_durable_paper_account():
    """Return durable PAPER v1 facts recovered from the restart-safe tables."""

    try:
        limit = request.args.get("limit", default=200, type=int)
        snapshot = postgres_durable_paper_account_provider(int(get_current_user_id()), limit)
        if snapshot is None:
            return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
        body = snapshot.to_public_dict()
        body["source"] = "qd_paper_execution_orders"
        body["recovery"] = {"status": "REPLAYED_FROM_DURABLE_FACTS", "network_access": False, "live_enabled": False}
        return jsonify(body), 200
    except (PaperExecutionAccountServiceError, TypeError, ValueError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503


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


@blp.route("/api/quant/testnet/execution/rehearsal/readonly", methods=["GET"])
@login_required
def get_readonly_gate_testnet_execution_rehearsal():
    """Return deterministic Gate TestNet order/fill lifecycle evidence.

    This endpoint is a local fixture rehearsal.  It never reads credentials,
    creates a client, sends a request, or mutates an order.
    """
    try:
        instrument_id = request.args.get("instrument_id", "BTC_USDT")
        market_type = request.args.get("market_type", "perpetual")
        fill_ratio = request.args.get("fill_ratio", "1")
        receipt = gate_testnet_execution_service_from_app(current_app).run(
            instrument_id=instrument_id, market_type=market_type, fill_ratio=fill_ratio
        )
    except (GateTestnetExecutionRehearsalServiceError, TypeError, ValueError):
        return jsonify({"status": "UNAVAILABLE", "network_access": False, "live_enabled": False}), 503
    return jsonify(receipt.to_public_dict()), 200


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


@blp.route("/api/quant/shadow/summary/readonly", methods=["GET"])
@login_required
def get_readonly_shadow_summary():
    """Return one authenticated, credential- and instrument-scoped Shadow Diff summary."""

    try:
        credential_id = int(request.args["credential_id"])
        exchange = request.args["exchange"]
        market_type = request.args["market_type"]
        account_scope = request.args["account_scope"]
        instrument_id = request.args.get("instrument_id", "")
        observed_at = request.args.get("as_of")
        as_of = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if observed_at else datetime.now(timezone.utc)
        status, body = shadow_summary_service_from_app(current_app).read_response(
            user_id=get_current_user_id(), credential_id=credential_id, exchange=exchange,
            market_type=market_type, account_scope=account_scope, instrument_id=instrument_id,
            as_of=as_of,
        )
    except (KeyError, ValueError, TypeError, ReadonlyShadowSummaryServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/gate/account/readonly", methods=["GET"])
@login_required
def get_readonly_gate_account():
    """Return injected, sanitized Gate balances/orders/fills evidence."""

    try:
        credential_id = int(request.args["credential_id"])
        market_type = request.args["market_type"]
        account_scope = request.args["account_scope"]
        instrument_id = request.args.get("instrument_id", "")
        observed_at = request.args.get("as_of")
        as_of = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if observed_at else datetime.now(timezone.utc)
        status, body = gate_account_service_from_app(current_app).read_response(
            user_id=get_current_user_id(), credential_id=credential_id,
            market_type=market_type, account_scope=account_scope,
            instrument_id=instrument_id, as_of=as_of,
        )
    except (KeyError, ValueError, TypeError, ReadonlyGateAccountServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/gate/account/unified/readonly", methods=["GET"])
@login_required
def get_readonly_gate_unified_account():
    """Return one same-credential Spot + Perpetual Gate read snapshot.

    This endpoint is all-or-nothing: a partial market read is returned as an
    unavailable response rather than being presented as a complete account.
    It is GET-only and never authorizes order or cancel operations.
    """

    try:
        credential_id = int(request.args["credential_id"])
        account_scope = request.args["account_scope"]
        instrument_id = request.args.get("instrument_id", "")
        observed_raw = request.args.get("as_of")
        as_of = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else datetime.now(timezone.utc)
        status, body = gate_unified_account_service_from_app(current_app).read_response(
            user_id=get_current_user_id(), credential_id=credential_id,
            account_scope=account_scope, instrument_id=instrument_id, as_of=as_of,
        )
    except (KeyError, ValueError, TypeError, ReadonlyGateUnifiedAccountServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/gate/account/unified/health/readonly", methods=["GET"])
@login_required
def get_readonly_gate_unified_account_health():
    """Return the sanitized Gate account read-health receipt only.

    This is a GET-only diagnostic surface.  It does not claim reconciliation
    health, authorize writes, or expose credential/provider payloads.
    """

    try:
        credential_id = int(request.args["credential_id"])
        account_scope = request.args["account_scope"]
        instrument_id = request.args.get("instrument_id", "")
        observed_raw = request.args.get("as_of")
        as_of = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else datetime.now(timezone.utc)
        status, body = gate_unified_account_service_from_app(current_app).read_health_response(
            user_id=get_current_user_id(), credential_id=credential_id,
            account_scope=account_scope, instrument_id=instrument_id, as_of=as_of,
        )
    except (KeyError, ValueError, TypeError, ReadonlyGateUnifiedAccountServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/gate/testnet/account", methods=["GET"])
@login_required
def get_gate_testnet_environment_account():
    """Read a real Gate TestNet account using an explicit local env source.

    This route is disabled unless ``QUANT_GATE_TESTNET_ENV_READ_ENABLED=1``.
    It is GET-only and returns sanitized typed facts; it has no order, cancel,
    executor, or Live capability. Encrypted database credentials remain the
    preferred production read provider above.
    """

    try:
        market_type = request.args.get("market_type", "spot")
        account_scope = request.args["account_scope"]
        instrument_id = request.args.get("instrument_id", "")
        credential_id_raw = request.args.get("credential_id")
        order_history = request.args.get("order_history", "0") == "1"

        # Prefer an explicitly selected, encrypted database credential when
        # supplied by the authenticated UI.  The provider is TestNet-only and
        # GET-only; no key/secret crosses this route and no write capability is
        # enabled.  Keep the environment-backed path below for local
        # operators that intentionally use it without a saved credential.
        if credential_id_raw is not None:
            credential_id = int(credential_id_raw)
            if credential_id <= 0:
                raise ValueError("credential_id must be positive")
            observed_at = request.args.get("as_of")
            as_of = datetime.fromisoformat(observed_at.replace("Z", "+00:00")) if observed_at else datetime.now(timezone.utc)
            status, body = gate_account_service_from_app(current_app).read_response(
                user_id=get_current_user_id(), credential_id=credential_id,
                market_type=market_type, account_scope=account_scope,
                instrument_id=instrument_id, as_of=as_of,
            )
            if status != 200:
                return jsonify(body), status
            body.update({
                "environment": "TESTNET",
                "network_access": True,
                "mock": False,
                "live_enabled": False,
                # Saved-credential reads are already scoped snapshots; do not
                # claim a separate history query that the provider did not run.
                "order_source": "saved_credential",
            })
            return jsonify(body), status

        snapshot = read_gate_testnet_environment_snapshot(
            market_type=market_type,
            account_scope=account_scope,
            instrument_id=instrument_id,
            order_history=order_history,
        )
        body = snapshot.to_public_dict()
        body.update({
            "status": "READY",
            "environment": "TESTNET",
            "network_access": True,
            "mock": False,
            "live_enabled": False,
            "order_source": "history" if order_history else "open",
        })
        return jsonify(body), 200
    except (KeyError, ValueError, TypeError, GateTestnetEnvReadError):
        return jsonify({"status": "UNAVAILABLE", "environment": "TESTNET", "network_access": False, "live_enabled": False}), 503


@blp.route("/api/quant/gate/market/readonly", methods=["GET"])
@login_required
def get_readonly_gate_market():
    """Return one explicit public Gate TestNet market-evidence bundle.

    The route has no credential or write capability.  It remains unavailable
    until ``QUANT_GATE_PUBLIC_MARKET_READ_ENABLED=1`` is explicitly set.
    """
    try:
        instrument_id = request.args["instrument_id"]
        market_type = GateMarketType(request.args.get("market_type", "spot").lower())
        interval = request.args.get("interval", "1m")
        candle_limit = request.args.get("candle_limit", default=100, type=int)
        depth_limit = request.args.get("depth_limit", default=20, type=int)
        observed_raw = request.args.get("observed_at")
        observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else datetime.now(timezone.utc)
        status, body = gate_public_market_service_from_app(current_app).read_response(
            instrument_id=instrument_id,
            market_type=market_type,
            interval=interval,
            candle_limit=candle_limit,
            depth_limit=depth_limit,
            observed_at=observed_at,
        )
    except (KeyError, ValueError, TypeError, GatePublicMarketServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/gate/market/unified/readonly", methods=["GET"])
@login_required
def get_readonly_gate_unified_market():
    """Return all-or-nothing Spot + Perpetual public market evidence."""

    try:
        instrument_id = request.args["instrument_id"]
        interval = request.args.get("interval", "1m")
        candle_limit = request.args.get("candle_limit", default=100, type=int)
        depth_limit = request.args.get("depth_limit", default=20, type=int)
        observed_raw = request.args.get("observed_at")
        observed_at = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else datetime.now(timezone.utc)
        status, body = gate_unified_market_service_from_app(current_app).read_response(
            instrument_id=instrument_id,
            interval=interval,
            candle_limit=candle_limit,
            depth_limit=depth_limit,
            observed_at=observed_at,
        )
    except (KeyError, ValueError, TypeError, ReadonlyGateUnifiedMarketServiceError):
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503
    return jsonify(body), status


@blp.route("/api/quant/product/rehearsal/readonly", methods=["GET"])
@login_required
def get_readonly_non_live_product_rehearsal():
    """Return the complete fixture-only non-live product rehearsal.

    This endpoint is deliberately a local deterministic evidence surface. It
    cannot read credentials, create a connection, call a venue, or mutate any
    account/order state.
    """
    try:
        return jsonify(build_offline_product_rehearsal()), 200
    except NonLiveProductRehearsalError:
        return jsonify({"status": "UNAVAILABLE", "live_enabled": False}), 503


@blp.route("/api/quant/entry/admit", methods=["POST"])
@login_required
def admit_runtime_entry():
    """Persist one authenticated Canonical Entry admission in PAPER/SHADOW.

    This endpoint is intentionally not an execution endpoint.  It composes
    authority resolution, durable entry, hard-risk, reservation, and outbox
    facts on one caller-owned transaction.  It has no venue/executor access
    and never accepts credentials or a LIVE mode.
    """

    try:
        result = admit_runtime_entry_payload(
            request.get_json(silent=True),
            tenant_id=int(get_current_user_id()),
            actor_id=str(get_current_user_id()),
        )
        return jsonify(result_to_public_dict(result)), 200
    except RuntimeEntryAdmissionApiError as exc:
        return jsonify({"status": "REJECTED", "code": "ENTRY_CONTRACT_INVALID", "message": str(exc), "live_enabled": False}), 422
    except Exception:
        # Repository, authority, risk, and outbox details stay behind the
        # typed API boundary; the DB context has already rolled back.
        return jsonify({"status": "UNAVAILABLE", "code": "ENTRY_ADMISSION_UNAVAILABLE", "live_enabled": False}), 503


@blp.route("/api/quant/runtime-entry/authority/project", methods=["POST"])
@login_required
def project_runtime_entry_authority():
    """Project one authenticated Gate snapshot into authority facts.

    The endpoint persists scope binding, instrument rules, and instrument
    authority facts from real Gate TestNet read evidence on one caller-owned
    transaction.  It never fabricates facts, never opens a venue client on its
    own, and is write-enabled only for the authenticated user's own credential.
    """

    from app.services.runtime_entry_authority_projection_service import (
        RuntimeEntryAuthorityProjectionError,
        RuntimeEntryAuthorityProjectionService,
    )
    from app.utils.db import get_db_connection

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        credential_id = int(payload.get("credential_id") or 0)
        account_scope = str(payload.get("account_scope") or "").strip()
        market_type = str(payload.get("market_type") or "").strip().lower()
        instrument_id = str(payload.get("instrument_id") or "").strip()
        observed_raw = payload.get("as_of")
        as_of = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else None
    except (TypeError, ValueError):
        return jsonify({"status": "REJECTED", "code": "PROJECT_CONTRACT_INVALID", "message": "invalid projection payload", "live_enabled": False}), 422
    if credential_id <= 0 or not account_scope or market_type not in {"spot", "perpetual"}:
        return jsonify({"status": "REJECTED", "code": "PROJECT_CONTRACT_INVALID", "message": "credential_id/account_scope/market_type are required", "live_enabled": False}), 422

    service = RuntimeEntryAuthorityProjectionService()
    try:
        with get_db_connection() as connection:
            result = service.project_authority_facts(
                connection,
                user_id=int(get_current_user_id()),
                credential_id=credential_id,
                account_scope=account_scope,
                market_type=market_type,
                instrument_id=instrument_id,
                as_of=as_of,
            )
            connection.commit()
    except RuntimeEntryAuthorityProjectionError as exc:
        return jsonify({"status": "UNAVAILABLE", "code": "AUTHORITY_PROJECTION_UNAVAILABLE", "message": str(exc), "live_enabled": False}), 503
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "AUTHORITY_PROJECTION_UNAVAILABLE", "live_enabled": False}), 503

    return jsonify({
        "status": "PROJECTED",
        "contract_version": "runtime-entry-authority-v1",
        "dispositions": result.dispositions,
        "snapshot_fingerprint": result.snapshot_fingerprint,
        "observed_at": result.observed_at.isoformat(),
        "live_enabled": False,
        "network_access": True,
        "writes_enabled": True,
    }), 200


@blp.route("/api/quant/runtime-entry/pipeline/run", methods=["POST"])
@login_required
def run_runtime_entry_pipeline():
    """Project authority facts, reconcile, and persist position subjects.

    The pipeline runs on one caller-owned transaction: snapshot -> scope/rule/
    authority facts -> reconciliation checkpoint (HEALTHY only when local and
    external facts match) -> position projections/subjects.  It never
    fabricates facts; a non-HEALTHY checkpoint leaves position subjects absent.
    """

    from app.services.runtime_entry_authority_projection_service import (
        RuntimeEntryAuthorityProjectionError,
        RuntimeEntryAuthorityProjectionService,
    )
    from app.utils.db import get_db_connection

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        credential_id = int(payload.get("credential_id") or 0)
        account_scope = str(payload.get("account_scope") or "").strip()
        market_type = str(payload.get("market_type") or "").strip().lower()
        instrument_id = str(payload.get("instrument_id") or "").strip()
        observed_raw = payload.get("as_of")
        as_of = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else None
    except (TypeError, ValueError):
        return jsonify({"status": "REJECTED", "code": "PIPELINE_CONTRACT_INVALID", "message": "invalid pipeline payload", "live_enabled": False}), 422
    if credential_id <= 0 or not account_scope or market_type not in {"spot", "perpetual"}:
        return jsonify({"status": "REJECTED", "code": "PIPELINE_CONTRACT_INVALID", "message": "credential_id/account_scope/market_type are required", "live_enabled": False}), 422

    service = RuntimeEntryAuthorityProjectionService()
    try:
        with get_db_connection() as connection:
            result = service.run_pipeline(
                connection,
                user_id=int(get_current_user_id()),
                credential_id=credential_id,
                account_scope=account_scope,
                market_type=market_type,
                instrument_id=instrument_id,
                as_of=as_of,
            )
            connection.commit()
    except RuntimeEntryAuthorityProjectionError as exc:
        return jsonify({"status": "UNAVAILABLE", "code": "RUNTIME_ENTRY_PIPELINE_UNAVAILABLE", "message": str(exc), "live_enabled": False}), 503
    except Exception:
        return jsonify({"status": "UNAVAILABLE", "code": "RUNTIME_ENTRY_PIPELINE_UNAVAILABLE", "live_enabled": False}), 503

    return jsonify({**result, "live_enabled": False}), 200


@blp.route("/api/quant/runtime-entry/risk-facts/project", methods=["POST"])
@login_required
def project_authoritative_risk_facts():
    """Project authoritative risk facts from a real Gate snapshot.

    Writes conservative policy, instrument rules, account facts, kill switches
    (all OFF), and market observations so Hard Risk can evaluate Open/Increase
    admission.  Repeated calls are idempotent (ON CONFLICT DO NOTHING).
    """

    from app.services.authoritative_risk_facts_projection_service import (
        AuthoritativeRiskFactsProjectionError,
        AuthoritativeRiskFactsProjectionService,
    )
    from app.utils.db import get_db_connection

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"status": "REJECTED", "code": "INVALID_JSON", "live_enabled": False}), 422
    try:
        credential_id = int(payload.get("credential_id") or 0)
        account_scope = str(payload.get("account_scope") or "").strip()
        market_type = str(payload.get("market_type") or "").strip().lower()
        instrument_id = str(payload.get("instrument_id") or "").strip()
        observed_raw = payload.get("as_of")
        as_of = datetime.fromisoformat(observed_raw.replace("Z", "+00:00")) if observed_raw else None
    except (TypeError, ValueError):
        return jsonify({"status": "REJECTED", "code": "RISK_PROJECTION_INVALID", "message": "invalid payload", "live_enabled": False}), 422
    if credential_id <= 0 or not account_scope or market_type not in {"spot", "perpetual"}:
        return jsonify({"status": "REJECTED", "code": "RISK_PROJECTION_INVALID", "message": "credential_id/account_scope/market_type required", "live_enabled": False}), 422

    service = AuthoritativeRiskFactsProjectionService()
    try:
        with get_db_connection() as connection:
            result = service.project(
                connection,
                user_id=int(get_current_user_id()),
                credential_id=credential_id,
                account_scope=account_scope,
                market_type=market_type,
                instrument_id=instrument_id,
                as_of=as_of,
            )
            connection.commit()
    except AuthoritativeRiskFactsProjectionError as exc:
        import logging; logging.getLogger(__name__).error("risk facts projection: %s", exc, exc_info=True)
        return jsonify({"status": "UNAVAILABLE", "code": "RISK_FACTS_PROJECTION_UNAVAILABLE", "message": str(exc), "live_enabled": False}), 503
    except Exception as exc:
        import logging; logging.getLogger(__name__).error("risk facts projection unexpected: %s", exc, exc_info=True)
        return jsonify({"status": "UNAVAILABLE", "code": "RISK_FACTS_PROJECTION_UNAVAILABLE", "live_enabled": False}), 503

    return jsonify({
        "status": "RISK_FACTS_PROJECTED",
        "disposition": result.disposition,
        "snapshot_fingerprint": result.snapshot_fingerprint,
        "live_enabled": False,
    }), 200


__all__ = ["blp"]
