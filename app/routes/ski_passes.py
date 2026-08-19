from flask import Blueprint, jsonify, request
from app.models.resort import Resort
from app.models.ski_pass import SkiPassSeason
from app.services.ski_passes import import_result, preview, replace_grid, serialize_season

bp_ski_passes = Blueprint("ski_passes", __name__, url_prefix="/api/forfaits")
bp_admin_ski_passes = Blueprint("admin_ski_passes", __name__, url_prefix="/api/admin/ski-passes")
bp_admin_station_ski_passes = Blueprint(
    "admin_station_ski_passes", __name__, url_prefix="/api/admin/stations"
)


def _season_for(slug, season_name=None):
    query = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
             .where(Resort.slug == slug))
    if season_name:
        query = query.where(SkiPassSeason.season == season_name)
    else:
        query = query.order_by(SkiPassSeason.season.desc())
    return query.first()


def _station_payload(slug):
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        payload = dict(payload)
        # The station-scoped URL is authoritative; the frontend does not need
        # to duplicate the slug in the imported JSON document.
        payload["station_slug"] = slug
    return payload


def _persist(payload, target_season=None):
    """Return a JSON response and never disguise a failed transaction as 200."""
    try:
        season, errors = replace_grid(payload, target_season=target_season)
        if errors:
            return jsonify({"success": False, "errors": errors}), 422
        result = import_result(season)
        if not result["periods_count"] or not result["passes_count"] or not result["prices_count"]:
            return jsonify({
                "success": False,
                "error": "empty_ski_pass_grid",
                "message": "Aucune grille tarifaire n'a été enregistrée",
            }), 422
        return jsonify(result), 200
    except Exception:
        # db.atomic() has already rolled the transaction back at this point.
        # Keep implementation/SQL details out of the response while giving the
        # editor a stable, actionable API error.
        return jsonify({
            "success": False,
            "error": "ski_pass_persistence_failed",
            "message": "L'enregistrement des forfaits a échoué; aucune modification n'a été appliquée",
        }), 500


@bp_ski_passes.get("/stations/<string:slug>")
def public_grid(slug):
    resort = Resort.get_or_none(Resort.slug == slug)
    if resort is None or not resort.is_active:
        return jsonify({"error": "station_not_found"}), 404
    season = _season_for(slug, request.args.get("season"))
    if season is None:
        return jsonify({"error": "ski_pass_season_not_found"}), 404
    return jsonify(serialize_season(season))


@bp_admin_ski_passes.get("/stations/<string:slug>")
def admin_grid(slug):
    if Resort.get_or_none(Resort.slug == slug) is None:
        return jsonify({"error": "station_not_found"}), 404
    season_name = request.args.get("season")
    if season_name:
        season = _season_for(slug, season_name)
        return (jsonify(serialize_season(season)), 200) if season else (jsonify({"error": "ski_pass_season_not_found"}), 404)
    seasons = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
               .where(Resort.slug == slug).order_by(SkiPassSeason.season.desc()))
    return jsonify([serialize_season(season) for season in seasons])


@bp_admin_station_ski_passes.get("/<string:slug>/ski-passes")
def admin_station_grids(slug):
    """Station-editor read contract; always returns a stable seasons envelope."""
    if Resort.get_or_none(Resort.slug == slug) is None:
        return jsonify({"error": "station_not_found", "message": "Station inexistante"}), 404
    seasons = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
               .where(Resort.slug == slug).order_by(SkiPassSeason.season.desc()))
    return jsonify({"seasons": [serialize_season(season) for season in seasons]})


@bp_admin_ski_passes.post("/import/preview")
def preview_import():
    payload = request.get_json(silent=True)
    result = preview(payload)
    return jsonify(result), 200 if result["valid"] else 422


@bp_admin_station_ski_passes.post("/<string:slug>/forfaits/preview")
def preview_station_import(slug):
    """Preview URL used by the station editor in the Vercel frontend."""
    result = preview(_station_payload(slug))
    return jsonify(result), 200 if result["valid"] else 422


@bp_admin_station_ski_passes.post("/<string:slug>/forfaits/import")
def confirm_station_import(slug):
    """Import URL used by the station editor in the Vercel frontend."""
    payload = _station_payload(slug)
    return _persist(payload)


@bp_admin_ski_passes.post("/import")
def confirm_import():
    return _persist(request.get_json(silent=True))


@bp_admin_station_ski_passes.put("/<string:slug>/ski-passes/<int:season_id>")
def update_station_grid(slug, season_id):
    """Transactionally save the complete editable normalized season."""
    existing = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
                .where((SkiPassSeason.id == season_id) & (Resort.slug == slug)).first())
    if existing is None:
        return jsonify({"error": "ski_pass_season_not_found", "message": "Saison inexistante"}), 404
    payload = _station_payload(slug)
    return _persist(payload, target_season=existing)


@bp_admin_ski_passes.delete("/stations/<string:slug>/seasons/<string:season_name>")
def delete_season(slug, season_name):
    season = _season_for(slug, season_name)
    if season is None:
        return jsonify({"error": "ski_pass_season_not_found"}), 404
    season.delete_instance(recursive=True)
    return "", 204
