# app/routes/admin_stations.py
from flask import Blueprint, request, jsonify, abort
from peewee import fn
from app.models.base import db
from app.models.resort import Resort
from app.models.station_widgets import StationWidgets
from app.services.public_cache import bump_public_resorts_version
import json
import re
import uuid

bp_admin_st = Blueprint("admin_stations", __name__, url_prefix="/api/admin/stations")


def deep_merge(dst, src):
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return src
    out = dict(dst)
    for k, v in src.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"^-+|-+$", "", s, flags=re.UNICODE)
    return s or str(uuid.uuid4())[:8]


def _normalize_forfait_columns(columns):
    if not isinstance(columns, list):
        return []
    out = []
    for i, col in enumerate(columns, start=1):
        c = col if isinstance(col, dict) else {}
        out.append({
            "id": str(c.get("id") or f"c-{i}"),
            "label": "" if c.get("label") is None else str(c.get("label")),
            "value": "" if c.get("value") is None else str(c.get("value")),
        })
    return out


def _normalize_forfait_item(item, idx):
    itm = item if isinstance(item, dict) else {}
    columns = _normalize_forfait_columns(itm.get("columns"))
    if columns:
        merged = dict(itm)
        merged["id"] = str(itm.get("id") or f"f-{idx}")
        merged["columns"] = columns
        return merged

    # Compatibilité rétroactive avec l'ancien format title/price/url
    legacy_columns = []
    if itm.get("title") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-1", "label": "title", "value": str(itm.get("title"))})
    if itm.get("price") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-2", "label": "price", "value": str(itm.get("price"))})
    if itm.get("url") not in (None, ""):
        legacy_columns.append({"id": f"c-{idx}-3", "label": "url", "value": str(itm.get("url"))})

    merged = dict(itm)
    merged["id"] = str(itm.get("id") or f"f-{idx}")
    merged["columns"] = legacy_columns
    return merged


def _normalize_widgets_config(cfg):
    if not isinstance(cfg, dict):
        return {}
    out = dict(cfg)
    forfaits = out.get("forfaits")
    if not isinstance(forfaits, dict):
        forfaits = {}
    items = forfaits.get("items")
    if not isinstance(items, list):
        items = []
    forfaits["items"] = [_normalize_forfait_item(item, i) for i, item in enumerate(items, start=1)]
    out["forfaits"] = forfaits
    return out


# ============ LIST ============
@bp_admin_st.get("/")
def list_resorts():
    q = Resort.select().order_by(Resort.name.asc())
    active_filter = request.args.get("active")
    if active_filter is not None:
        if active_filter.lower() not in {"true", "false"}:
            abort(400, "active doit être true ou false")
        q = q.where(Resort.is_active == (active_filter.lower() == "true"))
    data = []
    for r in q:
        data.append({
            "id": str(r.id),
            "slug": r.slug,
            "name": r.name,
            "latitude": r.latitude,
            "longitude": r.longitude,
            "region": r.region_name,
            "is_active": bool(r.is_active) if r.is_active is not None else True,
        })
    return jsonify({"items": data, "count": len(data)})


# ============ CREATE ============
@bp_admin_st.post("/")
def create_resort():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        abort(400, "name requis")

    slug = (payload.get("slug") or _slugify(name))
    if Resort.get_or_none(Resort.slug == slug):
        abort(409, "slug déjà existant")

    with db.atomic():
        r = Resort.create(
            id=payload.get("id") or str(uuid.uuid4()),
            name=name,
            slug=slug,

            # Activation
            is_active=payload.get("is_active", True),

            # Localisation
            region_id=payload.get("region_id"),
            region_name=payload.get("region_name"),
            country_code=payload.get("country_code"),
            department=payload.get("department"),

            # Géo
            latitude=payload.get("latitude"),
            longitude=payload.get("longitude"),

            # Altitudes
            altitude_base_m=payload.get("altitude_base_m"),
            altitude_top_m=payload.get("altitude_top_m"),
            altitude_min_m=payload.get("altitude_min_m"),
            altitude_max_m=payload.get("altitude_max_m"),

            # Domaine skiable
            lifts_count=payload.get("lifts_count"),
            pistes_count=payload.get("pistes_count"),
            ski_area_km=payload.get("ski_area_km"),

                    # Contenu / SEO
        website_url=payload.get("website_url"),
        cover_image_url=payload.get("cover_image_url"),
        logo_url=payload.get("logo_url"),   # ⬅️ AJOUT
        amenities=payload.get("amenities"),
        description_md=payload.get("description_md"),
        description_html=payload.get("description_html"),
        meta_title=payload.get("meta_title"),
        meta_description=payload.get("meta_description"),

            # Plan des pistes
            pistes_small_map_url=payload.get("pistes_small_map_url"),
            pistes_large_map_url=payload.get("pistes_large_map_url"),
            pistes_caption=payload.get("pistes_caption"),

            # Snowpark
            snowpark_map_url=payload.get("snowpark_map_url"),
            snowpark_caption=payload.get("snowpark_caption"),

            # Saison
            season_open_date=payload.get("season_open_date"),
            season_close_date=payload.get("season_close_date"),
        )

        # Widgets (facultatif)
        StationWidgets.create(
            station_slug=slug,
            config=StationWidgets.to_json({
                "stationSlug": slug,
                "pistes": {"enabled": False, "smallMapUrl": None, "largeMapUrl": None, "caption": None},
                "meteo": {"enabled": False, "iframeUrl": None},
                "description": {"enabled": False, "html": None, "metaTitle": None, "metaDescription": None},
                "forfaits": {"enabled": False, "items": []},
                "webcams": {"enabled": False, "items": []},
                "snow": {"enabled": False, "iframeUrl": None},
            })
        )

    bump_public_resorts_version()
    return jsonify({"ok": True, "resort": r.to_dict()}), 201


# ============ GET (fiche + widgets) ============
@bp_admin_st.get("/<string:slug>")
def get_resort_admin(slug):
    r = Resort.get_or_none(Resort.slug == slug)
    if not r:
        abort(404, "Not found")
    w = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    cfg = StationWidgets.from_json(w.config) if w else {}
    cfg = _normalize_widgets_config(cfg)

    return jsonify({
        "resort": r.to_dict(),
        "widgets": cfg or {
            "pistes": {"enabled": False},
            "description": {"enabled": False},
            "webcams": {"enabled": False, "items": []},
            "forfaits": {"enabled": False, "items": []},
            "meteo": {"enabled": False},
            "snow": {"enabled": False}
        }
    })


# ============ PATCH (maj complète des champs station) ============
@bp_admin_st.patch("/<string:slug>")
def patch_resort_admin(slug):
    r = Resort.get_or_none(Resort.slug == slug)
    if not r:
        abort(404, "Not found")
    payload = request.get_json(silent=True) or {}

    allowed_fields = [
        # Activation
        "is_active",

        # Identité / contenu / SEO
        "name", "website_url", "cover_image_url", "logo_url", "amenities",
        "description_md", "description_html",
        "meta_title", "meta_description",

        # Localisation
        "region_id", "region_name", "country_code", "department",

        # Géo
        "latitude", "longitude",

        # Altitudes
        "altitude_base_m", "altitude_top_m", "altitude_min_m", "altitude_max_m",

        # Domaine
        "lifts_count", "pistes_count", "ski_area_km",

        # Plan des pistes
        "pistes_small_map_url", "pistes_large_map_url", "pistes_caption",

        # Snowpark
        "snowpark_map_url", "snowpark_caption",

        # Saison
        "season_open_date", "season_close_date",
    ]


    unknown_fields = set(payload.keys()) - set(allowed_fields)
    if unknown_fields:
        abort(400, f"champs non autorisés: {', '.join(sorted(unknown_fields))}")

    if "is_active" in payload and not isinstance(payload.get("is_active"), bool):
        abort(400, "is_active doit être un booléen")

    is_active_changed = "is_active" in payload and bool(payload.get("is_active")) != bool(r.is_active)

    with db.atomic():
        for f in allowed_fields:
            if f in payload:
                setattr(r, f, payload[f])
        # le slug n’est pas modifié ici (stabilité des URLs)
        r.save()

    if is_active_changed:
        bump_public_resorts_version()

    return jsonify({"ok": True, "resort": r.to_dict()})


@bp_admin_st.patch("/bulk-activation")
def bulk_activation():
    payload = request.get_json(silent=True) or {}
    is_active = payload.get("is_active")
    if not isinstance(is_active, bool):
        abort(400, "is_active doit être un booléen")

    query = Resort.update({Resort.is_active: is_active})
    slug_prefix = payload.get("slug_prefix")
    if slug_prefix:
        query = query.where(Resort.slug.startswith(slug_prefix))
    region_id = payload.get("region_id")
    if region_id:
        query = query.where(Resort.region_id == region_id)

    updated = query.execute()
    if updated > 0:
        bump_public_resorts_version()
    return jsonify({"ok": True, "updated": updated, "is_active": is_active})


# ============ PATCH widgets (merge JSON) ============
@bp_admin_st.patch("/<string:slug>/widgets")
def patch_widgets_admin(slug):
    payload = request.get_json(silent=True) or {}
    payload = _normalize_widgets_config(payload)
    w = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    if not w:
        StationWidgets.create(station_slug=slug, config=json.dumps(payload))
        return jsonify({"ok": True, "created": True})
    current = StationWidgets.from_json(w.config)
    merged = deep_merge(current if isinstance(current, dict) else {}, payload)
    merged = _normalize_widgets_config(merged)
    w.config = StationWidgets.to_json(merged)
    w.save()
    return jsonify({"ok": True, "merged": True})
