from flask import Blueprint, jsonify
from peewee import fn

from app.models.region import Region
from app.models.resort import Resort
from app.routes.public_resorts import _resort_public_dict

bp_regions = Blueprint("regions_public", __name__)

REGIONS_FR = [
    {"id": "auvergne-rhone-alpes", "name": "Auvergne-Rhône-Alpes", "country_code": "FR"},
    {"id": "bourgogne-franche-comte", "name": "Bourgogne-Franche-Comté", "country_code": "FR"},
    {"id": "bretagne", "name": "Bretagne", "country_code": "FR"},
    {"id": "centre-val-de-loire", "name": "Centre-Val de Loire", "country_code": "FR"},
    {"id": "corse", "name": "Corse", "country_code": "FR"},
    {"id": "grand-est", "name": "Grand Est", "country_code": "FR"},
    {"id": "hauts-de-france", "name": "Hauts-de-France", "country_code": "FR"},
    {"id": "ile-de-france", "name": "Île-de-France", "country_code": "FR"},
    {"id": "normandie", "name": "Normandie", "country_code": "FR"},
    {"id": "nouvelle-aquitaine", "name": "Nouvelle-Aquitaine", "country_code": "FR"},
    {"id": "occitanie", "name": "Occitanie", "country_code": "FR"},
    {"id": "pays-de-la-loire", "name": "Pays de la Loire", "country_code": "FR"},
    {"id": "provence-alpes-cote-dazur", "name": "Provence-Alpes-Côte d’Azur", "country_code": "FR"},
]

@bp_regions.get("/api/regions")
def list_regions():
    """Retourne la liste complète des régions françaises"""
    return jsonify(sorted(REGIONS_FR, key=lambda r: r["name"])), 200


@bp_regions.get("/api/regions/<slug>")
def get_region(slug):
    """Return the content and every public station for a region landing page."""
    region = Region.get_or_none(fn.LOWER(Region.id) == slug.strip().lower())
    if region is None:
        return jsonify({"error": "region_not_found", "message": "Region not found"}), 404

    stations = (Resort.select()
                .where(
                    Resort.is_active
                    & (fn.LOWER(Resort.region_id) == region.id.lower())
                    & Resort.slug.is_null(False)
                    & (fn.TRIM(Resort.slug) != "")
                )
                .order_by(Resort.name.asc(), Resort.id.asc()))
    payload = region.to_dict()
    payload["stations"] = [_resort_public_dict(station) for station in stations]
    return jsonify(payload), 200
