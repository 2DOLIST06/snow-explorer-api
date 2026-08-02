import hmac
import os
from functools import wraps

from flask import current_app, jsonify, request


def authenticate_admin_request():
    """Return a 401 response unless the request carries the configured admin token."""
    # Browsers must be able to complete the CORS preflight before sending the
    # authenticated request itself.
    if request.method == "OPTIONS":
        return None

    expected = current_app.config.get("ADMIN_API_TOKEN") or os.getenv("ADMIN_API_TOKEN")
    supplied = request.headers.get("X-Admin-Token")
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        supplied = authorization[7:]

    if not expected or not supplied or not hmac.compare_digest(str(expected), supplied):
        return jsonify({"error": "admin_authentication_required"}), 401
    return None


def protect_admin_routes(app):
    """Require authentication for every current and future ``/api/admin`` route."""
    @app.before_request
    def require_admin_authentication():
        if request.path == "/api/admin" or request.path.startswith("/api/admin/"):
            return authenticate_admin_request()
        return None


def admin_required(view):
    """Authenticate sensitive administration endpoints with a configured token."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        unauthorized = authenticate_admin_request()
        if unauthorized is not None:
            return unauthorized
        return view(*args, **kwargs)
    return wrapped
