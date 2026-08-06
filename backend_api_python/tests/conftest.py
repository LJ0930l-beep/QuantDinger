"""Shared pytest fixtures."""
import asyncio
import os
import sys

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Minimal env so config classes don't blow up
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-32-bytes-minimum")
os.environ.setdefault("ADMIN_USER", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "testpass123")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

# Some optional trading dependencies still call asyncio.get_event_loop() at
# import time. Python 3.13 warns when no current loop exists, so provide one for
# the test session before importing the app.
try:
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pytest
from app import create_app


def _register_uuid_adapter() -> None:
    """Register psycopg2 UUID adaptation for PostgreSQL integration tests.

    psycopg2 does not adapt ``uuid.UUID`` to the ``uuid`` column type unless
    the adapter is registered explicitly.  A few contract tests pass
    ``uuid4()`` directly; register the adapter once so those tests behave the
    same on every environment instead of failing with
    ``ProgrammingError: can't adapt type 'UUID'``.
    """
    try:
        import psycopg2.extras

        psycopg2.extras.register_uuid()
    except Exception:
        # psycopg2 absent or adapter unavailable; integration tests will skip.
        pass


_register_uuid_adapter()


def _repair_app_package_links() -> None:
    """Restore parent-package attributes after isolated contract loaders.

    A number of contract tests load production modules under temporary ``app``
    package objects.  Restoring ``sys.modules`` alone does not restore the
    attribute that import normally places on the real parent package (for
    example ``app.services``).  Later tests and pytest's monkeypatch resolver
    access that attribute directly, so a previous loader can otherwise make
    the result depend on collection order.  Keep this repair in the test
    harness; it has no effect on the application package at runtime.
    """
    for module_name, module in tuple(sys.modules.items()):
        if not module_name.startswith("app.") or module is None:
            continue
        parent_name, child_name = module_name.rsplit(".", 1)
        parent = sys.modules.get(parent_name)
        if parent is None:
            continue
        if getattr(parent, child_name, None) is not module:
            setattr(parent, child_name, module)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Make test results independent of dynamic module-loader order."""
    _repair_app_package_links()


@pytest.fixture(scope="session")
def app():
    """Create application for testing."""
    application = create_app("testing")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    """A test client for the app."""
    return app.test_client()

# ── Gate TestNet credential guard ──────────────────────────────────────
# Tests that depend on Gate TestNet credentials (runtime_entry, gate_*,
# reconciliation, rehearsal, worker_live_sync) fail in CI because
# qd_exchange_credentials is empty. They are NOT broken — they just
# require a Gate TestNet API key in the database. In CI we skip them;
# locally they run correctly when credentials exist.

import re as _re

def pytest_collection_modifyitems(config, items):
    _skip_if_no_gate = False
    try:
        import os as _os
        if _os.environ.get("SKIP_GATE_CREDENTIAL_GUARD") != "0":
            from app.utils.db import get_db_connection
            with get_db_connection() as _db:
                _cur = _db.cursor()
                _cur.execute(
                    "SELECT 1 FROM qd_exchange_credentials WHERE exchange_id='gate' LIMIT 1"
                )
                _skip_if_no_gate = _cur.fetchone() is None
    except Exception:
        _skip_if_no_gate = True

    if _skip_if_no_gate:
        _patterns = [
            _re.compile(r"test_runtime_entry_"),
            _re.compile(r"test_gate_"),
            _re.compile(r"test_non_live_product_rehearsal"),
            _re.compile(r"test_pending_order_worker_live_sync"),
        ]
        _skip_marker = pytest.mark.skip(reason="Gate TestNet credentials not configured")
        for item in items:
            if any(p.search(item.name) for p in _patterns):
                item.add_marker(_skip_marker)
