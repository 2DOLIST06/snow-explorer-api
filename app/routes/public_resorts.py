from flask import Blueprint, jsonify, request
from app.models.resort import Resort
from functools import reduce
import operator

try:
    # Peewee >=3
    from peewee import Field, fn
except Exception:  # garde-fou si import différent
    Field = object
    fn = None  # type: ignore

bp_public = Blueprint("public_resorts", __name__, url_prefix="/api/resorts")


def _get_field(model, candidates):
    """Retourne le champ Peewee si présent et bien un Field, sinon None."""
    for name in candidates:
        if hasattr(model, name):
            attr = getattr(model, name)
            if isinstance(attr, Field):
                return attr
    return None


# Détecte les colonnes disponibles une seule fois
F_IS_ACTIVE = _get_field(Resort, ["is_active", "active"])
F_STATUS = _get_field(Resort, ["status"])
F_NAME = _get_field(Resort, ["name", "title", "label"])
F_SLUG = _get_field(Resort, ["slug", "slug_text", "slug_field"])


def _base_query():
    q = Resort.select()
    if F_IS_ACTIVE is not None:
        return q.where(F_IS_ACTIVE == True)
    if F_STATUS is not None:
        return q.where(F_STATUS == "published")
    return q


def _resort_public_dict(r: Resort) -> dict:
    """
    Dict public pour le front Next.js.
    On part de to_dict() puis on force/ajoute les champs nécessaires
    (logo_url, pistes_*, snowpark_*, saison, etc.).
    """
    base = {}
    if hasattr(r, "to_dict") and callable(getattr(r, "to_dict")):
        base = r.to_dict()
    else:
        base = {
            "id": r.id,
            "name": getattr(r, "name", None),
            "slug": getattr(r, "slug", None),
        }

    # Champs importants pour le front (qu'on force à exister dans le JSON)
    base["logo_url"] = getattr(r, "logo_url", None)

    # Plan des pistes
    base["pistes_large_map_url"] = getattr(r, "pistes_large_map_url", None)
    base["pistes_small_map_url"] = getattr(r, "pistes_small_map_url", None)
    base["pistes_caption"] = getattr(r, "pistes_caption", None)

    # Snowpark
    base["snowpark_map_url"] = getattr(r, "snowpark_map_url", None)
    base["snowpark_caption"] = getattr(r, "snowpark_caption", None)

    # Altitudes / saison (au cas où to_dict ne les gère pas)
    base["altitude_min_m"] = getattr(r, "altitude_min_m", None)
    base["altitude_max_m"] = getattr(r, "altitude_max_m", None)
    base["altitude_base_m"] = getattr(r, "altitude_base_m", None)
    base["altitude_top_m"] = getattr(r, "altitude_top_m", None)

    base["season_open_date"] = getattr(r, "season_open_date", None)
    base["season_close_date"] = getattr(r, "season_close_date", None)

    return base


@bp_public.get("/")
def list_resorts():
    q_str = (request.args.get("q") or "").strip()
    query = _base_query()

    if q_str:
        like = f"%{q_str}%"
        conds = []
        if F_NAME is not None:
            conds.append(F_NAME.ilike(like))
        if F_SLUG is not None:
            conds.append(F_SLUG.ilike(like))
        if conds:
            query = query.where(reduce(operator.or_, conds))

    if F_NAME is not None:
        query = query.order_by(F_NAME.asc())

    data = [_resort_public_dict(r) for r in query.limit(200)]
    return jsonify(data), 200


@bp_public.get("/<slug>")
def get_resort(slug: str):
    query = _base_query()

    # 1) Recherche par slug si vraie colonne
    if F_SLUG is not None:
        r = query.where(F_SLUG == slug).get_or_none()
        if r:
            return jsonify(_resort_public_dict(r)), 200

    # 2) Fallback par nom normalisé (si pas de colonne slug)
    if F_NAME is not None and fn is not None:
        slug_norm = slug.replace("-", " ").strip().lower()
        r = query.where(fn.LOWER(F_NAME) == slug_norm).get_or_none()
        if r:
            return jsonify(_resort_public_dict(r)), 200

        # Dernier filet: LIKE sur le nom
        r = query.where(F_NAME.ilike(f"%{slug_norm}%")).get_or_none()
        if r:
            return jsonify(_resort_public_dict(r)), 200

    return jsonify({"error": "not_found"}), 404
