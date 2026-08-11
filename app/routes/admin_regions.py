import re

from flask import Blueprint, jsonify, request
from peewee import IntegrityError

from app.datetime_utils import utcnow
from app.models.region import Region, slugify_region

bp_admin_regions = Blueprint("admin_regions", __name__, url_prefix="/api/admin/regions")
TAG_RE = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.IGNORECASE)
FIELDS = {"name", "slug", "country_code", "seo_text", "meta_title", "meta_description"}


def _validate(payload, creating=False):
    if not isinstance(payload, dict):
        return None, "invalid_request"
    unknown = set(payload) - FIELDS
    if unknown:
        return None, "unknown_fields"
    values = dict(payload)
    if creating and not isinstance(values.get("name"), str):
        return None, "name_required"
    if "name" in values:
        values["name"] = values["name"].strip()
        if not values["name"]:
            return None, "name_required"
    if creating and not values.get("slug"):
        values["slug"] = slugify_region(values["name"])
    elif "slug" in values:
        values["slug"] = slugify_region(values["slug"])
    if creating and not values.get("slug"):
        return None, "slug_required"
    if "country_code" in values:
        code = str(values["country_code"] or "").strip().upper()
        if len(code) != 2 or not code.isalpha():
            return None, "country_code_invalid"
        values["country_code"] = code
    elif creating:
        values["country_code"] = "FR"
    for field, maximum in (("meta_title", 70), ("meta_description", 170)):
        if field in values and values[field] is not None:
            if not isinstance(values[field], str) or len(values[field]) > maximum:
                return None, f"{field}_too_long"
    if "seo_text" in values and values["seo_text"] is not None:
        if not isinstance(values["seo_text"], str) or TAG_RE.search(values["seo_text"]):
            return None, "seo_text_must_be_plain_text"
    return values, None


@bp_admin_regions.get("")
def list_regions():
    return jsonify([region.to_dict() for region in Region.select().order_by(Region.name)]), 200


@bp_admin_regions.post("")
def create_region():
    values, error = _validate(request.get_json(silent=True), creating=True)
    if error:
        return jsonify({"error": error}), 400
    values["id"] = values["slug"]
    try:
        region = Region.create(**values)
    except IntegrityError:
        return jsonify({"error": "region_slug_conflict"}), 409
    return jsonify({"region": region.to_dict()}), 201


@bp_admin_regions.get("/<identifier>")
def get_region(identifier):
    region = Region.get_or_none(Region.id == identifier)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404
    return jsonify(region.to_dict()), 200


@bp_admin_regions.patch("/<identifier>")
def patch_region(identifier):
    region = Region.get_or_none(Region.id == identifier)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404
    values, error = _validate(request.get_json(silent=True))
    if error:
        return jsonify({"error": error}), 400
    for field, value in values.items():
        setattr(region, field, value)
    region.updated_at = utcnow()
    try:
        region.save()
    except IntegrityError:
        return jsonify({"error": "region_slug_conflict"}), 409
    return jsonify({"region": region.to_dict()}), 200
