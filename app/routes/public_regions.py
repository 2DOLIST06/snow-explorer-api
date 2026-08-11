from flask import Blueprint, jsonify, request
from peewee import JOIN

from app.models.region import Region
from app.models.resort import Resort

bp_regions = Blueprint("regions_public", __name__)


def _find_region(identifier):
    return Region.get_or_none((Region.id == identifier) | (Region.slug == identifier))


@bp_regions.get("/api/regions")
def list_regions():
    return jsonify([region.to_dict() for region in Region.select().order_by(Region.name)]), 200


@bp_regions.get("/api/regions/<identifier>")
def get_region(identifier):
    region = _find_region(identifier)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404
    return jsonify(region.to_dict()), 200


@bp_regions.get("/api/regions/<identifier>/resorts")
def get_region_resorts(identifier):
    region = _find_region(identifier)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404
    active = request.args.get("active")
    if active is not None and active.strip().lower() not in {"true", "false"}:
        return jsonify({"error": "active must be true or false"}), 400
    query = (Resort.select(Resort, Region)
             .join(Region, JOIN.INNER)
             .where(Resort.region == region.id)
             .order_by(Resort.name, Resort.id))
    if active is not None:
        query = query.where(Resort.is_active == (active.strip().lower() == "true"))
    return jsonify([resort.to_dict() for resort in query]), 200
