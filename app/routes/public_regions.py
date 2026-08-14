from flask import Blueprint, jsonify
from peewee import fn

from app.models.region import Region
from app.models.resort import Resort
from app.routes.public_resorts import _resort_public_dict
from app.services.region_ids import canonical_region_id, region_id_variants
from app.models.station_widgets import StationWidgets
from app.datetime_utils import isoformat_utc

bp_regions = Blueprint("regions_public", __name__)

@bp_regions.get("/api/regions")
def list_regions():
    """Retourne la liste complète des régions françaises"""
    regions = (Region.select()
               .where(Region.country_code == "FR")
               .order_by(Region.name.asc(), Region.id.asc()))
    payload = {}
    widgets_by_slug = {row.station_slug: row.updated_at for row in StationWidgets.select()}
    station_dates = {}
    for station in Resort.select().where(Resort.is_active):
        modified = max((value for value in (
            station.updated_at, widgets_by_slug.get(station.slug)
        ) if value is not None), default=None)
        region_id = canonical_region_id(station.region_id or "")
        if modified is not None and (region_id not in station_dates or modified > station_dates[region_id]):
            station_dates[region_id] = modified
    for region in regions:
        public_id = canonical_region_id(region.id)
        # Prefer the canonical row if a transition left both rows in the table.
        if public_id not in payload or region.id.strip().lower() == public_id:
            payload[public_id] = {
                "id": public_id,
                "name": region.name,
                "country_code": region.country_code,
                "updated_at": isoformat_utc(max(
                    (value for value in (region.updated_at, station_dates.get(public_id)) if value is not None),
                    default=None,
                )),
            }
    return jsonify(sorted(payload.values(), key=lambda item: (item["name"], item["id"]))), 200


@bp_regions.get("/api/regions/<slug>")
def get_region(slug):
    """Return the content and every public station for a region landing page."""
    requested_id = canonical_region_id(slug)
    variants = region_id_variants(requested_id)
    region = Region.get_or_none(fn.LOWER(fn.TRIM(Region.id)) == requested_id)
    if region is None:
        region = Region.get_or_none(fn.LOWER(fn.TRIM(Region.id)).in_(variants))
    if region is None:
        return jsonify({"error": "region_not_found", "message": "Region not found"}), 404

    stations = (Resort.select()
                .where(
                    Resort.is_active
                    & fn.LOWER(fn.TRIM(Resort.region_id)).in_(variants)
                    & Resort.slug.is_null(False)
                    & (fn.TRIM(Resort.slug) != "")
                )
                .order_by(Resort.name.asc(), Resort.id.asc()))
    payload = region.to_dict()
    station_rows = list(stations)
    widget_dates = {
        row.station_slug: row.updated_at
        for row in StationWidgets.select().where(
            StationWidgets.station_slug.in_([station.slug for station in station_rows])
        )
    } if station_rows else {}
    payload["stations"] = [
        _resort_public_dict(station, widgets_updated_at=widget_dates.get(station.slug))
        for station in station_rows
    ]
    station_modified = [
        value for station in station_rows
        for value in (station.updated_at, widget_dates.get(station.slug))
        if value is not None
    ]
    payload["updated_at"] = isoformat_utc(max(
        ([region.updated_at] if region.updated_at is not None else []) + station_modified,
        default=None,
    ))
    return jsonify(payload), 200
