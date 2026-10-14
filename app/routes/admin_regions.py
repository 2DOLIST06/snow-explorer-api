from flask import Blueprint, jsonify, request
from peewee import fn

from app.models.region import Region
from app.services.resort_json import sanitize_html
from app.datetime_utils import utcnow
from app.services.public_cache import invalidate_region
from app.services.region_ids import canonical_region_id, region_id_variants


bp_admin_regions = Blueprint(
    "admin_regions", __name__, url_prefix="/api/admin/regions"
)
EDITABLE_FIELDS = {"description_html", "meta_title", "meta_description"}


def _find_region(slug):
    requested_id = canonical_region_id(slug)
    stored_id = fn.LOWER(fn.TRIM(Region.id))
    region = Region.get_or_none(stored_id == requested_id)
    if region is None:
        region = Region.get_or_none(stored_id.in_(region_id_variants(requested_id)))
    return region


@bp_admin_regions.get("", strict_slashes=False)
def list_regions_admin():
    regions = Region.select().order_by(Region.name.asc(), Region.id.asc())
    payload = {}
    for region in regions:
        public_id = canonical_region_id(region.id)
        if public_id not in payload or region.id.strip().lower() == public_id:
            payload[public_id] = region.to_dict()
    return jsonify(sorted(payload.values(), key=lambda item: (item["name"], item["id"]))), 200


@bp_admin_regions.get("/<slug>")
def get_region_admin(slug):
    region = _find_region(slug)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404
    return jsonify({"region": region.to_dict()}), 200


@bp_admin_regions.patch("/<slug>")
def patch_region_admin(slug):
    region = _find_region(slug)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_payload"}), 400
    unknown = set(payload) - EDITABLE_FIELDS
    if unknown:
        return jsonify({"error": "unknown_fields", "fields": sorted(unknown)}), 400

    for field, value in payload.items():
        if value is not None and not isinstance(value, str):
            return jsonify({"error": f"{field}_must_be_a_string"}), 400
        if field == "description_html" and value is not None:
            value = sanitize_html(value)
        setattr(region, field, value)
    if payload:
        region.updated_at = utcnow()
        region.save()
        invalidate_region(region.id)
    return jsonify({"ok": True, "region": region.to_dict()}), 200
