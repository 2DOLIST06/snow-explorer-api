"""Preflight endpoints for the ANMSM administration API.

The ANMSM business routes disable Flask's generated ``OPTIONS`` handlers.
Keep preflight handling separate so it can never enter authentication or the
logo import/review code.
"""

from flask import Blueprint


bp_admin_anmsm_cors = Blueprint("admin_anmsm_cors", __name__)

ANMSM_LOGO_ROUTES = (
    "/api/admin/anmsm/logos",
    "/api/admin/anmsm/logos/sync",
    "/api/admin/anmsm/logos/bulk-approve",
    "/api/admin/anmsm/logos/bulk-ignore",
    "/api/admin/anmsm/logos/bulk-reprocess",
)


def _preflight():
    return "", 204


for index, route in enumerate(ANMSM_LOGO_ROUTES):
    bp_admin_anmsm_cors.add_url_rule(
        route,
        endpoint=f"preflight_{index}",
        view_func=_preflight,
        methods=["OPTIONS"],
        provide_automatic_options=False,
    )
