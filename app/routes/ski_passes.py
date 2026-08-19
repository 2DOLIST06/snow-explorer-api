from flask import Blueprint, jsonify, request
from app.models.resort import Resort
from app.models.ski_pass import SkiPassSeason
from app.services.ski_passes import preview, replace_grid, serialize_season

bp_ski_passes = Blueprint("ski_passes", __name__, url_prefix="/api/forfaits")
bp_admin_ski_passes = Blueprint("admin_ski_passes", __name__, url_prefix="/api/admin/ski-passes")


def _season_for(slug, season_name=None):
    query = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
             .where(Resort.slug == slug))
    if season_name:
        query = query.where(SkiPassSeason.season == season_name)
    else:
        query = query.order_by(SkiPassSeason.season.desc())
    return query.first()


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


@bp_admin_ski_passes.post("/import/preview")
def preview_import():
    payload = request.get_json(silent=True)
    result = preview(payload)
    return jsonify(result), 200 if result["valid"] else 422


@bp_admin_ski_passes.post("/import")
def confirm_import():
    season, errors = replace_grid(request.get_json(silent=True))
    if errors:
        result = preview(request.get_json(silent=True))
        return jsonify(result), 422
    return jsonify({"ok": True, "grid": serialize_season(season)}), 200


@bp_admin_ski_passes.delete("/stations/<string:slug>/seasons/<string:season_name>")
def delete_season(slug, season_name):
    season = _season_for(slug, season_name)
    if season is None:
        return jsonify({"error": "ski_pass_season_not_found"}), 404
    season.delete_instance(recursive=True)
    return "", 204
