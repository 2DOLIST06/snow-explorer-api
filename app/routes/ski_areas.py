import re
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from peewee import IntegrityError, OperationalError

from app.datetime_utils import utcnow
from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from app.services.public_cache import (cached_json, invalidate_ski_areas,
                                       ski_area_key, ski_areas_list_key)

bp_public_ski_areas = Blueprint("public_ski_areas", __name__, url_prefix="/api/ski-areas")
bp_admin_ski_areas = Blueprint("admin_ski_areas", __name__, url_prefix="/api/admin/ski-areas")
bp_station_ski_areas = Blueprint("station_ski_areas", __name__, url_prefix="/api/admin/stations")

TEXT_FIELDS = {"description", "cover_image_url", "piste_map_url", "season", "source"}
COUNT_FIELDS = {"altitude_min_m", "altitude_max_m", "ski_area_km", "pistes_count",
                "green_pistes_count", "blue_pistes_count", "red_pistes_count",
                "black_pistes_count", "lifts_count"}
DATE_FIELDS = {"forecast_open_date", "forecast_close_date"}
EDITABLE_FIELDS = TEXT_FIELDS | COUNT_FIELDS | DATE_FIELDS | {"name", "slug", "status", "verified_at"}


def _error(code, message, status=400, fields=None):
    body = {"error": code, "message": message}
    if fields:
        body["fields"] = fields
    return jsonify(body), status


def _pagination(default=20, maximum=100):
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", default))
    except (TypeError, ValueError):
        raise ValueError("page and per_page must be integers")
    if page < 1 or per_page < 1 or per_page > maximum:
        raise ValueError(f"page must be >= 1 and per_page must be between 1 and {maximum}")
    return page, per_page


def _iso(value):
    return value.isoformat() if value is not None else None


def _station_json(station):
    return {"id": str(station.id), "name": station.name, "slug": station.slug,
            "cover_image_url": station.cover_image_url, "logo_url": station.logo_url}


def _area_json(area, admin=False, stations=None):
    data = {"id": area.id, "name": area.name, "slug": area.slug, "status": area.status,
            "description": area.description, "cover_image_url": area.cover_image_url,
            "piste_map_url": area.piste_map_url, "altitude_min_m": area.altitude_min_m,
            "altitude_max_m": area.altitude_max_m, "ski_area_km": area.ski_area_km,
            "pistes_count": area.pistes_count, "green_pistes_count": area.green_pistes_count,
            "blue_pistes_count": area.blue_pistes_count, "red_pistes_count": area.red_pistes_count,
            "black_pistes_count": area.black_pistes_count, "lifts_count": area.lifts_count,
            "forecast_open_date": _iso(area.forecast_open_date),
            "forecast_close_date": _iso(area.forecast_close_date), "season": area.season,
            "updated_at": _iso(area.updated_at)}
    if admin:
        data.update(source=area.source, verified_at=_iso(area.verified_at), created_at=_iso(area.created_at))
    if stations is not None:
        data["stations"] = [_station_json(s) for s in stations]
    return data


def _parse_payload(payload, creating=False):
    if not isinstance(payload, dict):
        return None, _error("invalid_json", "A JSON object is required")
    unknown = set(payload) - EDITABLE_FIELDS - {"station_ids"}
    if unknown:
        return None, _error("unknown_fields", "Unknown fields", fields=sorted(unknown))
    values, errors = {}, {}
    for key in EDITABLE_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key in TEXT_FIELDS:
            if value is not None and not isinstance(value, str): errors[key] = "must be a string or null"
            else: values[key] = value.strip() or None if isinstance(value, str) else None
        elif key in COUNT_FIELDS:
            if value is None: values[key] = None
            elif isinstance(value, bool) or not isinstance(value, int) or value < 0: errors[key] = "must be a non-negative integer or null"
            else: values[key] = value
        elif key in DATE_FIELDS:
            if value in (None, ""): values[key] = None
            elif not isinstance(value, str): errors[key] = "must be YYYY-MM-DD or null"
            else:
                try: values[key] = date.fromisoformat(value)
                except ValueError: errors[key] = "must be a valid YYYY-MM-DD date"
        elif key == "verified_at":
            if value in (None, ""): values[key] = None
            elif not isinstance(value, str): errors[key] = "must be an ISO 8601 datetime or null"
            else:
                try: values[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError: errors[key] = "must be a valid ISO 8601 datetime"
        elif key == "status":
            if value not in ("draft", "published"): errors[key] = "must be draft or published"
            else: values[key] = value
        else:
            if not isinstance(value, str) or not value.strip(): errors[key] = "must be a non-empty string"
            else: values[key] = value.strip()
    if creating:
        for required in ("name", "slug"):
            if required not in values: errors[required] = "is required"
    if "slug" in values and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", values["slug"]):
        errors["slug"] = "must contain lowercase letters, digits and single hyphens"
    low, high = values.get("altitude_min_m"), values.get("altitude_max_m")
    opening, closing = values.get("forecast_open_date"), values.get("forecast_close_date")
    if low is not None and high is not None and low > high: errors["altitude_max_m"] = "must be >= altitude_min_m"
    if opening is not None and closing is not None and opening > closing: errors["forecast_close_date"] = "must be >= forecast_open_date"
    if errors: return None, _error("validation_error", "Payload validation failed", fields=errors)
    return values, None


def _station_ids(payload):
    if "station_ids" not in payload: return None, None
    ids = payload["station_ids"]
    if not isinstance(ids, list) or any(not isinstance(v, str) or not v for v in ids):
        return None, _error("validation_error", "station_ids must be an array of non-empty strings")
    if len(ids) != len(set(ids)): return None, _error("duplicate_station", "station_ids contains duplicates", 409)
    found = list(Resort.select().where(Resort.id.in_(ids))) if ids else []
    if len(found) != len(ids): return None, _error("station_not_found", "One or more stations do not exist", 404)
    return found, None


def _linked_stations(area_id, published_only=False):
    query = (Resort.select().join(SkiAreaResort).where(SkiAreaResort.ski_area == area_id))
    if published_only: query = query.where(Resort.is_active == True)
    return list(query.order_by(Resort.name, Resort.id))


@bp_admin_ski_areas.get("")
@bp_admin_ski_areas.get("/")
def admin_list():
    try: page, per_page = _pagination()
    except ValueError as exc: return _error("invalid_pagination", str(exc))
    query = SkiArea.select()
    search = (request.args.get("q") or "").strip()
    status = request.args.get("status")
    if search: query = query.where((SkiArea.name ** f"%{search}%") | (SkiArea.slug ** f"%{search}%"))
    if status:
        if status not in ("draft", "published"): return _error("validation_error", "status must be draft or published")
        query = query.where(SkiArea.status == status)
    total = query.count()
    items = [_area_json(row, admin=True) for row in query.order_by(SkiArea.name, SkiArea.id).paginate(page, per_page)]
    return jsonify({"items": items, "pagination": {"page": page, "per_page": per_page, "total": total,
                    "pages": (total + per_page - 1) // per_page}})


@bp_admin_ski_areas.post("")
@bp_admin_ski_areas.post("/")
def admin_create():
    payload = request.get_json(silent=True)
    values, error = _parse_payload(payload, True)
    if error: return error
    stations, error = _station_ids(payload)
    if error: return error
    try:
        with SkiArea._meta.database.atomic():
            area = SkiArea.create(**values)
            for station in stations or []: SkiAreaResort.create(ski_area=area, resort=station)
    except IntegrityError: return _error("slug_conflict", "This slug already exists", 409)
    invalidate_ski_areas(*(s.slug for s in stations or []))
    return jsonify({"ski_area": _area_json(area, True, stations or [])}), 201


def _get_admin(area_id):
    area = SkiArea.get_or_none(SkiArea.id == area_id)
    return area if area else _error("ski_area_not_found", "Ski area not found", 404)


@bp_admin_ski_areas.get("/<int:area_id>")
def admin_detail(area_id):
    area = _get_admin(area_id)
    if isinstance(area, tuple): return area
    return jsonify({"ski_area": _area_json(area, True, _linked_stations(area.id))})


@bp_admin_ski_areas.patch("/<int:area_id>")
def admin_patch(area_id):
    area = _get_admin(area_id)
    if isinstance(area, tuple): return area
    payload = request.get_json(silent=True)
    values, error = _parse_payload(payload)
    if error: return error
    stations, error = _station_ids(payload)
    if error: return error
    # Cross-field rules must also account for retained values on PATCH.
    low = values.get("altitude_min_m", area.altitude_min_m); high = values.get("altitude_max_m", area.altitude_max_m)
    opening = values.get("forecast_open_date", area.forecast_open_date); closing = values.get("forecast_close_date", area.forecast_close_date)
    if low is not None and high is not None and low > high: return _error("validation_error", "Invalid altitude range", fields={"altitude_max_m": "must be >= altitude_min_m"})
    if opening is not None and closing is not None and opening > closing: return _error("validation_error", "Invalid date range", fields={"forecast_close_date": "must be >= forecast_open_date"})
    old_stations = _linked_stations(area.id)
    try:
        with SkiArea._meta.database.atomic():
            for key, value in values.items(): setattr(area, key, value)
            area.updated_at = utcnow(); area.save()
            if stations is not None:
                SkiAreaResort.delete().where(SkiAreaResort.ski_area == area).execute()
                for station in stations: SkiAreaResort.create(ski_area=area, resort=station)
    except IntegrityError: return _error("slug_conflict", "This slug already exists", 409)
    current = stations if stations is not None else old_stations
    invalidate_ski_areas(*(s.slug for s in old_stations + current))
    return jsonify({"ski_area": _area_json(area, True, current)})


@bp_admin_ski_areas.post("/<int:area_id>/publish")
@bp_admin_ski_areas.post("/<int:area_id>/unpublish")
def publication(area_id):
    area = _get_admin(area_id)
    if isinstance(area, tuple): return area
    area.status = "published" if request.path.endswith("/publish") else "draft"
    area.updated_at = utcnow()
    with SkiArea._meta.database.atomic(): area.save()
    stations = _linked_stations(area.id); invalidate_ski_areas(*(s.slug for s in stations))
    return jsonify({"ski_area": _area_json(area, True, stations)})


@bp_admin_ski_areas.post("/<int:area_id>/stations/<station_id>")
def add_station(area_id, station_id):
    area = SkiArea.get_or_none(SkiArea.id == area_id); station = Resort.get_or_none(Resort.id == station_id)
    if not area: return _error("ski_area_not_found", "Ski area not found", 404)
    if not station: return _error("station_not_found", "Station not found", 404)
    try:
        with SkiArea._meta.database.atomic(): SkiAreaResort.create(ski_area=area, resort=station)
    except IntegrityError: return _error("relation_conflict", "This relation already exists", 409)
    invalidate_ski_areas(station.slug)
    return jsonify({"ski_area_id": area.id, "station_id": str(station.id)}), 201


@bp_admin_ski_areas.delete("/<int:area_id>/stations/<station_id>")
def remove_station(area_id, station_id):
    with SkiArea._meta.database.atomic():
        deleted = SkiAreaResort.delete().where((SkiAreaResort.ski_area == area_id) & (SkiAreaResort.resort == station_id)).execute()
        if deleted:
            from app.services.ski_area_catalog import mark_membership_ignored
            mark_membership_ignored(area_id, station_id)
    if not deleted: return _error("relation_not_found", "Relation not found", 404)
    station = Resort.get_or_none(Resort.id == station_id); invalidate_ski_areas(station.slug if station else "")
    return "", 204


@bp_admin_ski_areas.get("/station-options")
def station_options():
    try: page, per_page = _pagination(default=50)
    except ValueError as exc: return _error("invalid_pagination", str(exc))
    query = Resort.select(); search = (request.args.get("q") or "").strip()
    if search: query = query.where((Resort.name ** f"%{search}%") | (Resort.slug ** f"%{search}%"))
    total = query.count(); rows = query.order_by(Resort.name, Resort.id).paginate(page, per_page)
    return jsonify({"items": [_station_json(s) for s in rows], "pagination": {"page": page, "per_page": per_page, "total": total, "pages": (total + per_page - 1)//per_page}})


@bp_station_ski_areas.get("/<station_id>/ski-areas")
def admin_station_areas(station_id):
    station = Resort.get_or_none(Resort.id == station_id)
    if not station: return _error("station_not_found", "Station not found", 404)
    areas = SkiArea.select().join(SkiAreaResort).where(SkiAreaResort.resort == station.id).order_by(SkiArea.name, SkiArea.id)
    return jsonify({"station": _station_json(station), "ski_areas": [_area_json(a, True) for a in areas]})


@bp_station_ski_areas.put("/<station_id>/ski-areas")
def replace_station_areas(station_id):
    station = Resort.get_or_none(Resort.id == station_id)
    if not station: return _error("station_not_found", "Station not found", 404)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("ski_area_ids"), list):
        return _error("validation_error", "ski_area_ids must be an array")
    ids = payload["ski_area_ids"]
    if any(isinstance(v, bool) or not isinstance(v, int) for v in ids): return _error("validation_error", "ski_area_ids must contain integers")
    if len(ids) != len(set(ids)): return _error("relation_conflict", "ski_area_ids contains duplicates", 409)
    areas = list(SkiArea.select().where(SkiArea.id.in_(ids))) if ids else []
    if len(areas) != len(ids): return _error("ski_area_not_found", "One or more ski areas do not exist", 404)
    with SkiArea._meta.database.atomic():
        old_ids = {link.ski_area_id for link in SkiAreaResort.select().where(SkiAreaResort.resort == station)}
        SkiAreaResort.delete().where(SkiAreaResort.resort == station).execute()
        for area in areas: SkiAreaResort.create(ski_area=area, resort=station)
        from app.services.ski_area_catalog import mark_membership_ignored
        for removed_id in old_ids - set(ids): mark_membership_ignored(removed_id, station.id)
    invalidate_ski_areas(station.slug)
    return jsonify({"station": _station_json(station), "ski_areas": [_area_json(a, True) for a in areas]})


@bp_public_ski_areas.get("")
@bp_public_ski_areas.get("/")
@cached_json(lambda: ski_areas_list_key(), "PUBLIC_CACHE_DIRECTORY_TTL_SECONDS")
def public_list():
    try: page, per_page = _pagination()
    except ValueError as exc: return _error("invalid_pagination", str(exc))
    query = SkiArea.select().where(SkiArea.status == "published"); total = query.count()
    items = [_area_json(a) for a in query.order_by(SkiArea.name, SkiArea.id).paginate(page, per_page)]
    return jsonify({"items": items, "pagination": {"page": page, "per_page": per_page, "total": total, "pages": (total + per_page - 1)//per_page}})


@bp_public_ski_areas.get("/<slug>")
@cached_json(lambda slug: ski_area_key(slug), "PUBLIC_CACHE_DIRECTORY_TTL_SECONDS")
def public_detail(slug):
    area = SkiArea.get_or_none((SkiArea.slug == slug) & (SkiArea.status == "published"))
    if not area: return _error("ski_area_not_found", "Ski area not found", 404)
    return jsonify({"ski_area": _area_json(area, stations=_linked_stations(area.id, True))})


def public_station_domains(station):
    try:
        areas = list(SkiArea.select().join(SkiAreaResort).where(
            (SkiAreaResort.resort == station.id) & (SkiArea.status == "published")
        ).order_by(SkiArea.name, SkiArea.id))
    except OperationalError:
        # Keeps the historical station endpoint usable during a rolling deploy
        # until the additive migration has reached every environment.
        return []
    if not areas: return []
    ids = [a.id for a in areas]
    rows = (SkiAreaResort.select(SkiAreaResort, Resort).join(Resort)
            .where((SkiAreaResort.ski_area.in_(ids)) & (Resort.is_active == True) & (Resort.id != station.id))
            .order_by(Resort.name, Resort.id))
    grouped = {area_id: [] for area_id in ids}; seen = {area_id: set() for area_id in ids}
    for link in rows:
        area_id, linked_station = link.ski_area_id, link.resort
        if linked_station.id not in seen[area_id]:
            grouped[area_id].append(linked_station)
            seen[area_id].add(linked_station.id)
    return [{**_area_json(area), "stations": [_station_json(s) for s in grouped[area.id]]} for area in areas]
