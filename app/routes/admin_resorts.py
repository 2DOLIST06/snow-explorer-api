# app/routes/admin_resorts.py
from flask import Blueprint, jsonify, request
from datetime import date
from app.models.resort import Resort

bp_admin = Blueprint("admin_resorts", __name__, url_prefix="/api/admin/resorts")

# -------- utils --------
def _slugify(name: str) -> str:
    import re, unicodedata
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")

def _find_by_slug(slug: str) -> Resort | None:
    # pas de colonne 'slug' en DB → on compare slug(name)
    for r in Resort.select():
        if _slugify(r.name or "") == slug:
            return r
    return None

ALLOWED = {
    "name",
    "latitude", "longitude",
    "website_url", "cover_image_url", "description_md",
    "region_id", "department",
    "altitude_base_m", "altitude_top_m", "altitude_min_m", "altitude_max_m",
    "season_open_date", "season_close_date",
    "ski_area_km", "lifts_count", "pistes_count",
    "amenities",
}
INTS  = {"altitude_base_m", "altitude_top_m", "altitude_min_m", "altitude_max_m", "ski_area_km", "lifts_count", "pistes_count"}
FLTS  = {"latitude", "longitude"}
DATES = {"season_open_date", "season_close_date"}

def _i(v):
    if v in (None, ""): return None
    try: return int(v)
    except: return None

def _f(v):
    if v in (None, ""): return None
    try: return float(v)
    except: return None

def _d(v):
    if not v: return None
    try: return date.fromisoformat(v)  # YYYY-MM-DD
    except: return None

# -------- routes --------
@bp_admin.get("/")
def list_admin_resorts():
    data = [r.to_dict() for r in Resort.select().order_by(Resort.name.asc())]
    return jsonify(data), 200

@bp_admin.get("/<slug>")
def get_admin_resort(slug: str):
    r = _find_by_slug(slug)
    if not r:
        return jsonify({"error": "not_found"}), 404
    return jsonify(r.to_dict()), 200

@bp_admin.patch("/<slug>")
def patch_admin_resort(slug: str):
    r = _find_by_slug(slug)
    if not r:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json(silent=True) or {}
    for k, v in payload.items():
        if k not in ALLOWED:
            continue
        # ⬇️ ignore les champs qui n'existent pas dans le modèle/table
        if k not in Resort._meta.fields:
            continue

        if k in INTS:
            setattr(r, k, _i(v))
        elif k in FLTS:
            setattr(r, k, _f(v))
        elif k in DATES:
            setattr(r, k, _d(v))
        else:
            setattr(r, k, (v if v != "" else None))

    r.save()
    return jsonify(r.to_dict()), 200
