from flask import Blueprint, jsonify

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
