from flask import Blueprint, jsonify, request
from app.models.resort import Resort
from app.models.base import db
from app.models.ski_pass import SkiPassSeason
from app.models.station_widgets import StationWidgets
from app.routes.stations_widgets import _canonical_forfaits
from app.services.public_cache import bump_public_resorts_version
from app.services.ski_passes import import_result, preview, replace_grid, serialize_season

bp_ski_passes = Blueprint("ski_passes", __name__, url_prefix="/api/forfaits")
bp_admin_ski_passes = Blueprint("admin_ski_passes", __name__, url_prefix="/api/admin/ski-passes")
bp_admin_station_ski_passes = Blueprint(
    "admin_station_ski_passes", __name__, url_prefix="/api/admin/stations"
)


def _season_for(slug, season_name=None, active_only=False):
    query = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
             .where(Resort.slug == slug))
    if season_name:
        query = query.where(SkiPassSeason.season == season_name)
    else:
        query = query.order_by(SkiPassSeason.season.desc())
    if active_only:
        query = query.where(SkiPassSeason.is_active == True)
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
    season = _season_for(slug, request.args.get("season"), active_only=True)
    if season is None:
        return jsonify({"error": "ski_pass_season_not_found"}), 404
    return jsonify(serialize_season(season))


@bp_ski_passes.get("/stations/<string:slug>/systems")
def public_systems(slug):
    """Expose the two public-display decisions without inferring from data."""
    resort = Resort.get_or_none(Resort.slug == slug)
    if resort is None or not resort.is_active:
        return jsonify({"error": "station_not_found"}), 404

    widgets_row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    widgets = StationWidgets.from_json(widgets_row.config) if widgets_row else {}
    legacy = _canonical_forfaits(widgets)
    active_seasons = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
                      .where((Resort.slug == slug) & (SkiPassSeason.is_active == True))
                      .order_by(SkiPassSeason.season.desc()))
    normalized = [serialize_season(season) for season in active_seasons]
    return jsonify({
        "legacy_enabled": legacy["enabled"],
        "normalized_enabled": bool(normalized),
        "legacy": legacy,
        "normalized_seasons": normalized,
    })


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


@bp_admin_station_ski_passes.patch("/<string:slug>/ski-passes/<int:season_id>")
def set_station_season_activation(slug, season_id):
    """Change public visibility without modifying or deleting tariff data."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"is_active"} or not isinstance(payload["is_active"], bool):
        return jsonify({"error": "invalid_payload", "message": "is_active doit être un booléen"}), 400
    season = (SkiPassSeason.select(SkiPassSeason, Resort).join(Resort)
              .where((SkiPassSeason.id == season_id) & (Resort.slug == slug)).first())
    if season is None:
        return jsonify({"error": "ski_pass_season_not_found", "message": "Saison inexistante"}), 404

    changed = bool(season.is_active) != payload["is_active"]
    with db.atomic():
        if payload["is_active"]:
            # The public normalized endpoint serves one grid, so activation is
            # exclusive per resort and is committed atomically.
            (SkiPassSeason.update(is_active=False)
             .where((SkiPassSeason.resort == season.resort) & (SkiPassSeason.id != season.id))
             .execute())
        season.is_active = payload["is_active"]
        season.save(only=[SkiPassSeason.is_active])
    if changed:
        bump_public_resorts_version()
    return jsonify({"ok": True, "season": serialize_season(season)})


@bp_admin_ski_passes.delete("/stations/<string:slug>/seasons/<string:season_name>")
def delete_season(slug, season_name):
    season = _season_for(slug, season_name)
    if season is None:
        return jsonify({"error": "ski_pass_season_not_found"}), 404
    season.delete_instance(recursive=True)
    return "", 204
