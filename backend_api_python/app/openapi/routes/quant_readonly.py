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


__all__ = ["blp"]
