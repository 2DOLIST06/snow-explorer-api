from flask import Blueprint, jsonify
from peewee import fn

from app.models.region import Region
from app.models.resort import Resort
from app.routes.public_resorts import _resort_public_dict
from app.services.region_ids import canonical_region_id, region_id_variants

bp_regions = Blueprint("regions_public", __name__)

@bp_regions.get("/api/regions")
def list_regions():
    """Retourne la liste complète des régions françaises"""
    regions = (Region.select()
               .where(Region.country_code == "FR")
               .order_by(Region.name.asc(), Region.id.asc()))
    payload = {}
    for region in regions:
        public_id = canonical_region_id(region.id)
        # Prefer the canonical row if a transition left both rows in the table.
        if public_id not in payload or region.id.strip().lower() == public_id:
            payload[public_id] = {
                "id": public_id,
                "name": region.name,
                "country_code": region.country_code,
                "updated_at": region.updated_at.isoformat() if region.updated_at else None,
            }
    return jsonify(sorted(payload.values(), key=lambda item: (item["name"], item["id"]))), 200


@bp_regions.get("/api/regions/<slug>")
def get_region(slug):
    """Return the content and every public station for a region landing page."""
    requested_id = canonical_region_id(slug)
    variants = region_id_variants(requested_id)
    region = Region.get_or_none(fn.LOWER(fn.TRIM(Region.id)) == requested_id)
    if region is None:
        region = Region.get_or_none(fn.LOWER(fn.TRIM(Region.id)).in_(variants))
    if region is None:
        return jsonify({"error": "region_not_found", "message": "Region not found"}), 404

    stations = (Resort.select()
                .where(
                    Resort.is_active
                    & fn.LOWER(fn.TRIM(Resort.region_id)).in_(variants)
                    & Resort.slug.is_null(False)
                    & (fn.TRIM(Resort.slug) != "")
                )
                .order_by(Resort.name.asc(), Resort.id.asc()))
    payload = region.to_dict()
    payload["stations"] = [_resort_public_dict(station) for station in stations]
    return jsonify(payload), 200
