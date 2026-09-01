from flask import Blueprint, jsonify

from app.services.public_cache import invalidate_station, purge_all, purge_directory

bp_admin_cache = Blueprint("admin_cache", __name__, url_prefix="/api/admin/cache")


@bp_admin_cache.post("/stations/<string:slug>/purge")
def purge_station_cache(slug):
    return jsonify({"ok": True, "deleted": invalidate_station(slug)}), 200


@bp_admin_cache.post("/resorts/purge")
def purge_resorts_cache():
    return jsonify({"ok": True, "deleted": purge_directory()}), 200


@bp_admin_cache.post("/public/purge")
def purge_public_cache():
    return jsonify({"ok": True, "deleted": purge_all()}), 200
