from flask import Blueprint, Response, jsonify
from peewee import DatabaseError

from app.models.resort import Resort
from app.services.meteo_france_bra import BraError, BraService, IMAGES

bp_station_avalanche_risk = Blueprint("station_avalanche_risk", __name__, url_prefix="/api/stations")


def station(slug):
    return Resort.get_or_none((Resort.slug == slug) & (Resort.is_active == True))


def error(code, status):
    response = jsonify({"error": code})
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@bp_station_avalanche_risk.get("/<slug>/avalanche-risk")
def avalanche_risk(slug):
    try:
        resort = station(slug)
        if resort is None:
            return error("station_not_found", 404)
        result = BraService().risk(resort)
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response
    except DatabaseError:
        return error("avalanche_service_unavailable", 503)


@bp_station_avalanche_risk.get("/<slug>/avalanche-risk/pdf")
def bulletin_pdf(slug):
    return proxy_asset(slug, "pdf")


@bp_station_avalanche_risk.get("/<slug>/avalanche-risk/image/<kind>")
def bulletin_image(slug, kind):
    if kind not in IMAGES:
        return error("asset_not_found", 404)
    return proxy_asset(slug, kind)


def proxy_asset(slug, kind):
    try:
        resort = station(slug)
        if resort is None:
            return error("station_not_found", 404)
        service = BraService()
        mapping = service.mapping(resort)
        if mapping["mapping_status"] != "confirmed":
            return error("massif_not_identified", 404)
        content, mime = service.asset(mapping["id"], kind)
        response = Response(content, content_type=mime)
        # These routes expose documents, never a claim that they are current.
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Disposition"] = ('inline; filename="meteo-france-bra.pdf"' if kind == "pdf"
                                                   else 'inline; filename="meteo-france-bra.png"')
        return response
    except BraError as exc:
        return error("asset_not_found", 404) if str(exc) == "not_found" else error("avalanche_service_unavailable", 503)
    except DatabaseError:
        return error("avalanche_service_unavailable", 503)
