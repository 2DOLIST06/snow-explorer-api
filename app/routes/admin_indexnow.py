"""Authenticated IndexNow URL submission endpoint."""
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from flask import Blueprint, current_app, jsonify, request


bp_admin_indexnow = Blueprint("admin_indexnow", __name__, url_prefix="/api/admin")
logger = logging.getLogger("app.indexnow")
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
CANONICAL_HOST = "www.snow-explorer.com"
ALLOWED_HOSTS = {CANONICAL_HOST, "snow-explorer.com"}
MAX_URLS = 10_000


def _validated_urls(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("urls"), list):
        raise ValueError("Le champ 'urls' doit être une liste non vide.")
    urls = payload["urls"]
    if not urls:
        raise ValueError("Le champ 'urls' doit contenir au moins une URL.")
    if len(urls) > MAX_URLS:
        raise ValueError(f"Un envoi ne peut pas contenir plus de {MAX_URLS} URL.")

    for url in urls:
        if not isinstance(url, str) or not url or len(url) > 2048:
            raise ValueError("Chaque URL doit être une chaîne HTTPS valide.")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or
                parsed.username is not None or parsed.password is not None or
                parsed.port not in (None, 443) or not parsed.path.startswith("/")):
            raise ValueError(
                "Chaque URL doit être une URL HTTPS de snow-explorer.com ou www.snow-explorer.com."
            )
    return urls


def _indexnow_error(status):
    messages = {
        400: "Requête IndexNow invalide.",
        403: "La clé IndexNow n'est pas valide pour ce domaine.",
        422: "IndexNow a refusé une ou plusieurs URL.",
        429: "Trop de requêtes envoyées à IndexNow.",
    }
    return messages.get(status, f"IndexNow a répondu avec le statut HTTP {status}.")


@bp_admin_indexnow.post("/indexnow")
def submit_indexnow():
    try:
        urls = _validated_urls(request.get_json(silent=True))
    except (ValueError, TypeError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    key = current_app.config.get("INDEXNOW_KEY")
    if not key:
        logger.error("IndexNow submission refused: INDEXNOW_KEY is not configured")
        return jsonify({"success": False, "error": "IndexNow n'est pas configuré sur le serveur."}), 503

    body = json.dumps({
        "host": CANONICAL_HOST,
        "key": key,
        "keyLocation": f"https://{CANONICAL_HOST}/{key}.txt",
        "urlList": urls,
    }).encode("utf-8")
    upstream_request = Request(
        INDEXNOW_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urlopen(upstream_request, timeout=10) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
    except (URLError, TimeoutError, OSError) as exc:
        logger.error("IndexNow submission failed urls=%d error=%s", len(urls), type(exc).__name__)
        return jsonify({"success": False, "error": "IndexNow est temporairement injoignable."}), 502

    if status in (200, 202):
        logger.info("IndexNow submission urls=%d status=%d", len(urls), status)
        return jsonify({"success": True, "submitted": len(urls)})
    logger.warning("IndexNow submission failed urls=%d status=%d", len(urls), status)
    return jsonify({"success": False, "error": _indexnow_error(status), "indexnow_status": status}), 502
