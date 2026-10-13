from flask import Blueprint, jsonify

from app.models.resort import Resort
from app.services.meteo_france import MeteoFranceService

bp_station_weather = Blueprint("station_weather", __name__, url_prefix="/api/stations")


@bp_station_weather.get("/<slug>/weather")
def station_weather(slug):
    resort = Resort.get_or_none((Resort.slug == slug) & (Resort.is_active == True))
    if resort is None:
        return jsonify({"error": "station_not_found"}), 404
    response = jsonify(MeteoFranceService().weather(resort))
    # Freshness is recalculated on every request, independently of Redis TTL.
    response.headers["Cache-Control"] = "no-store"
    return response
