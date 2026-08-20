from flask import Blueprint, current_app, jsonify, request
from app.models.resort import Resort
from app.models.station_widgets import StationWidgets
from app.services.public_resort import get_public_resort
from app.services.public_cache import get_public_resorts_version
from functools import reduce
import operator

from peewee import Field, fn, prefetch

from app.models.ski_pass import (
    SkiPassPeriod,
    SkiPassPrice,
    SkiPassProduct,
    SkiPassSeason,
)
from app.services.ski_passes import decimal_json

bp_public = Blueprint("public_resorts", __name__, url_prefix="/api/resorts")
bp_public_stations = Blueprint(
    "public_stations", __name__, url_prefix="/api/stations"
)

MAX_LIMIT = 200


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
    """Build the public-navigation query (never includes drafts)."""
    return Resort.select().where(
        Resort.is_active
        & Resort.slug.is_null(False)
        & (fn.TRIM(Resort.slug) != "")
    )


def _requested_limit():
    raw_limit = request.args.get("limit")
    if raw_limit is None:
        # The unfiltered public endpoint is also the navigation source for the
        # SSR frontend, so omitting pagination must not silently truncate it.
        return None
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        limit = 0
    if limit <= 0 or limit > MAX_LIMIT or str(limit) != raw_limit.strip():
        raise ValueError(f"limit must be a positive integer no greater than {MAX_LIMIT}")
    return limit


def _resort_public_dict(r: Resort, snowparks_count=None) -> dict:
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
    base["snowparks_count"] = snowparks_count

    # Altitudes / saison (au cas où to_dict ne les gère pas)
    base["altitude_min_m"] = getattr(r, "altitude_min_m", None)
    base["altitude_max_m"] = getattr(r, "altitude_max_m", None)
    base["altitude_base_m"] = getattr(r, "altitude_base_m", None)
    base["altitude_top_m"] = getattr(r, "altitude_top_m", None)

    base["season_open_date"] = getattr(r, "season_open_date", None)
    base["season_close_date"] = getattr(r, "season_close_date", None)

    # Activation: forcé dans la réponse publique même si to_dict/fallback diverge
    is_active = getattr(r, "is_active", None)
    base["is_active"] = bool(is_active) if is_active is not None else True
    base["resort_is_active"] = base["is_active"]

    return base


@bp_public.get("/")
def list_resorts():
    active = request.args.get("active")
    if active is not None and active.strip().lower() != "true":
        return jsonify({"error": "active must be true on the public resorts endpoint"}), 400

    try:
        limit = _requested_limit()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    q_str = (request.args.get("q") or "").strip()
    try:
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
            # Name is the existing technical ordering; id makes ties deterministic.
            query = query.order_by(F_NAME.asc(), Resort.id.asc())

        if limit is not None:
            query = query.limit(limit)

        resorts = list(query)
        snowparks_counts = {}
        if resorts:
            widget_rows = StationWidgets.select().where(
                StationWidgets.station_slug.in_([resort.slug for resort in resorts])
            )
            for widget_row in widget_rows:
                config = StationWidgets.from_json(widget_row.config)
                snowparks = config.get("snowparks")
                count = snowparks.get("count") if isinstance(snowparks, dict) else None
                snowparks_counts[widget_row.station_slug] = (
                    count
                    if isinstance(count, int)
                    and not isinstance(count, bool)
                    and count >= 0
                    else None
                )

        data = [
            _resort_public_dict(r, snowparks_counts.get(r.slug))
            for r in resorts
        ]
    except Exception:
        current_app.logger.exception("Unable to retrieve public stations")
        return jsonify({"error": "Unable to retrieve stations"}), 500
    response = jsonify(data)
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=3600"
    response.headers["X-Public-Resorts-Version"] = str(get_public_resorts_version())
    return response, 200


def _get_resort_response(slug: str):
    data = get_public_resort(slug)
    if data is None:
        return jsonify({"error": "resort_not_found", "message": "Station not found"}), 404
    response = jsonify(data)
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=3600"
    response.headers["X-Public-Resorts-Version"] = str(get_public_resorts_version())
    return response, 200


def _station_snowparks_count(slug):
    widget_row = StationWidgets.get_or_none(StationWidgets.station_slug == slug)
    if widget_row is None:
        return None
    config = StationWidgets.from_json(widget_row.config)
    snowparks = config.get("snowparks")
    count = snowparks.get("count") if isinstance(snowparks, dict) else None
    return (
        count
        if isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        else None
    )


def _station_active_ski_pass(resort_id):
    """Return one active normalized grid, fetched in four bounded queries."""
    seasons = (
        SkiPassSeason.select()
        .where(
            (SkiPassSeason.resort == resort_id)
            & (SkiPassSeason.is_active == True)
        )
        .order_by(SkiPassSeason.season.desc(), SkiPassSeason.id.desc())
        .limit(1)
    )
    rows = prefetch(
        seasons,
        SkiPassPeriod.select(),
        SkiPassProduct.select(),
        SkiPassPrice.select(),
    )
    if not rows:
        return None

    season = rows[0]
    periods = sorted(season.periods, key=lambda row: (row.sort_order, row.id))
    products = sorted(season.products, key=lambda row: (row.sort_order, row.id))
    return {
        "id": season.id,
        "season": season.season,
        "currency": season.currency,
        "source_url": season.source_url,
        "is_active": bool(season.is_active),
        "periods": [
            {
                "id": period.id,
                "external_id": period.external_id,
                "name": period.name,
                "start_date": period.start_date.isoformat(),
                "end_date": period.end_date.isoformat(),
                "sort_order": period.sort_order,
            }
            for period in periods
        ],
        "passes": [
            {
                "id": product.id,
                "external_id": product.external_id,
                "name": product.name,
                "duration_days": product.duration_days,
                "duration_label": product.duration_label,
                "sort_order": product.sort_order,
                "prices": [
                    {
                        "id": price.id,
                        "period_id": price.period_id,
                        "category": price.category,
                        "category_label": price.category_label,
                        "price_type": price.price_type,
                        "price": decimal_json(price.price),
                        "price_min": decimal_json(price.price_min),
                        "price_max": decimal_json(price.price_max),
                        "dynamic_label": price.dynamic_label,
                        "sort_order": price.sort_order,
                    }
                    for price in sorted(
                        product.prices, key=lambda row: (row.sort_order, row.id)
                    )
                ],
            }
            for product in products
        ],
    }


def _get_station_response(slug: str):
    try:
        # Deliberately query by slug: this endpoint never loads the station list.
        resort = Resort.get_or_none(
            (Resort.slug == slug) & (Resort.is_active == True)
        )
        if resort is None:
            return jsonify({"error": "station_not_found", "message": "Station not found"}), 404

        data = _resort_public_dict(resort, _station_snowparks_count(slug))
        data["ski_pass"] = _station_active_ski_pass(resort.id)
    except Exception:
        current_app.logger.exception("Unable to retrieve public station %s", slug)
        return jsonify({"error": "Unable to retrieve station"}), 500

    response = jsonify(data)
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=3600"
    response.headers["X-Public-Resorts-Version"] = str(get_public_resorts_version())
    return response, 200


@bp_public.get("/<slug>")
def get_resort(slug: str):
    return _get_resort_response(slug)


@bp_public_stations.get("/<slug>")
def get_station(slug: str):
    """Return one station and its optional active normalized ski-pass grid."""
    return _get_station_response(slug)
