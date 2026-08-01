import hmac
import os
from functools import wraps

from flask import current_app, jsonify, request


def admin_required(view):
    """Authenticate sensitive administration endpoints with a configured token."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        expected = current_app.config.get("ADMIN_API_TOKEN") or os.getenv("ADMIN_API_TOKEN")
        supplied = request.headers.get("X-Admin-Token")
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied = authorization[7:]
        if not expected or not supplied or not hmac.compare_digest(str(expected), supplied):
            return jsonify({"error": "admin_authentication_required"}), 401
        return view(*args, **kwargs)
    return wrapped
