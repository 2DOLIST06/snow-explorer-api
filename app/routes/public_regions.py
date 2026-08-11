from flask import Blueprint, jsonify, request
from peewee import PeeweeException

from app.models.region import Region, slugify_region
from app.models.resort import Resort

bp_regions = Blueprint("regions_public", __name__)

# Compatibility data is intentionally retained: deployments historically had
# only this list and rollout of the SQL migration is not atomic with the app.
REGIONS_FR = [
    ("auvergne-rhone-alpes", "Auvergne-Rhône-Alpes"),
    ("bourgogne-franche-comte", "Bourgogne-Franche-Comté"),
    ("bretagne", "Bretagne"), ("centre-val-de-loire", "Centre-Val de Loire"),
    ("corse", "Corse"), ("grand-est", "Grand Est"),
    ("hauts-de-france", "Hauts-de-France"), ("ile-de-france", "Île-de-France"),
    ("normandie", "Normandie"), ("nouvelle-aquitaine", "Nouvelle-Aquitaine"),
    ("occitanie", "Occitanie"), ("pays-de-la-loire", "Pays de la Loire"),
    ("provence-alpes-cote-d-azur", "Provence-Alpes-Côte d’Azur"),
]


def _legacy_regions():
    return [{"id": identifier, "name": name, "slug": slugify_region(name),
             "country_code": "FR", "seo_text": None, "meta_title": None,
             "meta_description": None} for identifier, name in REGIONS_FR]


def _find_region(identifier):
    try:
        region = Region.get_or_none((Region.id == identifier) | (Region.slug == identifier))
        if region:
            return region.to_dict()
    except PeeweeException:
        pass
    return next((item for item in _legacy_regions()
                 if identifier in {item["id"], item["slug"]}), None)


@bp_regions.get("/api/regions")
def list_regions():
    try:
        persisted = [region.to_dict() for region in Region.select().order_by(Region.name)]
    except PeeweeException:
        persisted = []
    # Do not make public navigation depend on migration timing. Persisted rows
    # override compatibility rows and add their SEO fields when available.
    by_slug = {region["slug"]: region for region in _legacy_regions()}
    by_slug.update({region["slug"]: region for region in persisted})
    regions = sorted(by_slug.values(), key=lambda region: region["name"])
    return jsonify(regions), 200


@bp_regions.get("/api/regions/<identifier>")
def get_region(identifier):
    region = _find_region(identifier)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404
    return jsonify(region), 200


@bp_regions.get("/api/regions/<identifier>/resorts")
def get_region_resorts(identifier):
    region = _find_region(identifier)
    if region is None:
        return jsonify({"error": "region_not_found"}), 404
    active = request.args.get("active")
    if active is not None and active.strip().lower() not in {"true", "false"}:
        return jsonify({"error": "active must be true or false"}), 400
    query = (Resort.select()
             .where((Resort.region_id == region["id"]) |
                    (Resort.region_id == identifier) |
                    (Resort.region_name == region["name"]))
             .order_by(Resort.name, Resort.id))
    if active is not None:
        query = query.where(Resort.is_active == (active.strip().lower() == "true"))
    return jsonify([resort.to_dict() for resort in query]), 200
