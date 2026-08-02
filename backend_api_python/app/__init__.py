"""QuantDinger Python API Flask application factory."""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime
from pathlib import Path

try:
    from dotenv import load_dotenv

    _backend_dir = Path(__file__).resolve().parents[1]
    load_dotenv(_backend_dir / ".env", override=False)
    load_dotenv(_backend_dir.parent / ".env", override=False)
except Exception:
    pass

from flask import Flask
from flask.json.provider import DefaultJSONProvider
from flask_cors import CORS

from app.startup import (
    get_pending_order_worker as get_pending_order_worker,
    get_trading_executor as get_trading_executor,
    run_startup_hooks,
)
from app.utils.logger import get_logger, setup_logger
from app.utils.timeutil import to_utc_iso


logger = get_logger(__name__)


class SafeJSONProvider(DefaultJSONProvider):
    """JSON provider that normalizes NaN/Inf and datetime values."""

    @staticmethod
    def default(o):
        if isinstance(o, datetime):
            return to_utc_iso(o)
        if isinstance(o, date):
            return o.isoformat()
        return DefaultJSONProvider.default(o)

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("default", self.default)
        return _safe_json_dumps(obj, **kwargs)


def _safe_json_dumps(obj, **kwargs):
    return json.dumps(_sanitize(obj), **kwargs)


def _sanitize(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, datetime):
        return to_utc_iso(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def _configure_cors(app: Flask) -> None:
    origins = [
        o.strip() for o in os.getenv(
            "FRONTEND_URL",
            "http://localhost:8888,http://localhost:8000",
        ).split(",")
        if o.strip()
    ]
    capacitor_origins = [
        "https://localhost",
        "http://localhost",
        "capacitor://localhost",
        "ionic://localhost",
        "https://localhost:*",
        "http://localhost:*",
    ]
    for origin in capacitor_origins:
        if origin not in origins:
            origins.append(origin)

    CORS(app, origins=origins, supports_credentials=False, send_wildcard=False)
    logger.info(f"CORS allowed origins: {origins}")


def _configure_ibkr_asyncio() -> None:
    try:
        from ib_insync import util as ib_util
        ib_util.patchAsyncio()
        logger.info("ib_insync: patchAsyncio enabled for stable IBKR connections")
    except Exception as exc:
        logger.debug(f"ib_insync patchAsyncio skipped (ib_insync not installed?): {exc}")


def _bootstrap_database() -> None:
    try:
        from app.utils.db import get_db_type, init_database
        logger.info(f"Database type: {get_db_type()}")
        init_database()

        from app.runtime.roles import ProcessRole, current_process_role

        if current_process_role() in {ProcessRole.API, ProcessRole.LEGACY}:
            from app.services.user_service import get_user_service

            get_user_service().ensure_admin_exists()

            try:
                from app.services.builtin_indicators import upgrade_builtin_indicator_samples

                upgrade_builtin_indicator_samples()
            except Exception as sample_exc:
                logger.warning(f"Builtin indicator sample upgrade skipped: {sample_exc}")
    except Exception as e:
        logger.warning(f"Database initialization note: {e}")


def create_app(config_name='default', *, register_http_routes: bool = True):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.json_provider_class = SafeJSONProvider
    app.json = SafeJSONProvider(app)
    app.config['JSON_AS_ASCII'] = False

    if register_http_routes:
        _configure_cors(app)
    setup_logger()

    if register_http_routes:
        from app.observability import init_http_observability

        init_http_observability(app)

    from app.utils.auth import _configure_jwt_secret_warnings
    _configure_jwt_secret_warnings()

    _configure_ibkr_asyncio()
    _bootstrap_database()

    if register_http_routes:
        from app.routes import register_routes

        register_routes(app)
        # The built-in catalog is read-only metadata for the Strategy Factory.
        # It is safe as the default because it contains no credentials,
        # account facts, execution calls, or live authority.  Deployments may
        # replace it with an explicitly reviewed provider before serving.
        from app.services.builtin_strategy_catalog import builtin_strategy_catalog
        from app.services.readonly_projection_summary_service import postgres_projection_summary_provider
        from app.services.readonly_reconciliation_summary_service import postgres_reconciliation_summary_provider
        from app.services.readonly_shadow_summary_service import postgres_shadow_summary_provider
        from app.services.readonly_backtest_report_service import postgres_backtest_report_provider
        from app.services.readonly_paper_account_service import postgres_paper_account_provider
        from app.services.gate_testnet_rehearsal_file_provider import provider_from_path as gate_rehearsal_provider_from_path

        app.extensions.setdefault("readonly_strategy_catalog_provider", builtin_strategy_catalog)
        # This provider is SELECT-only and returns UNAVAILABLE when the
        # projection schema/database is not configured.  It does not replace
        # the stricter G4-B receipt required by /api/quant/readonly.
        app.extensions.setdefault("readonly_projection_summary_provider", postgres_projection_summary_provider)
        app.extensions.setdefault("readonly_reconciliation_summary_provider", postgres_reconciliation_summary_provider)
        app.extensions.setdefault("readonly_shadow_summary_provider", postgres_shadow_summary_provider)
        # This provider accepts only canonical, fingerprint-verified report
        # JSON. Legacy result_json rows remain unavailable rather than being
        # guessed into typed backtest facts.
        app.extensions.setdefault("readonly_backtest_report_provider", postgres_backtest_report_provider)
        app.extensions.setdefault("readonly_paper_account_provider", postgres_paper_account_provider)
        # An explicitly supplied, sanitized public-read artifact can feed the
        # read-only TestNet evidence endpoint.  No default path is guessed and
        # no credentials or venue client are loaded here.
        rehearsal_path = os.environ.get("QUANT_TESTNET_REHEARSAL_EVIDENCE_PATH", "").strip()
        if rehearsal_path:
            app.extensions.setdefault("readonly_gate_testnet_rehearsal_provider", gate_rehearsal_provider_from_path(rehearsal_path))
    run_startup_hooks(app)

    return app
