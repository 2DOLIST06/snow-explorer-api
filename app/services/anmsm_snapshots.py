from app.datetime_utils import utcnow
from app.models.anmsm_station_snapshot import AnmsmStationSnapshot


def record_logo_snapshot(stations, observed_at=None):
    """Persist a complete, successfully parsed logo feed for later read-only use."""
    observed_at = observed_at or utcnow()
    database = AnmsmStationSnapshot._meta.database
    # Rolling deployments may briefly run application code before the additive
    # migration.  Media synchronization must remain available in that window.
    if not database.table_exists(AnmsmStationSnapshot._meta.table_name):
        return False
    with database.atomic():
        for station in stations:
            external_id = station["external_station_id"]
            logo = station.get("logo") or {}
            row = AnmsmStationSnapshot.get_or_none(
                AnmsmStationSnapshot.external_station_id == external_id)
            values = {
                "station_name": station.get("external_name") or external_id,
                "logo_available": bool(logo.get("url")),
                "logo_url": logo.get("url"),
                "logo_seen_at": observed_at,
                "station_catalog_seen_at": observed_at,
                "last_seen_at": observed_at,
            }
            if row:
                for key, value in values.items():
                    setattr(row, key, value)
                row.save()
            else:
                AnmsmStationSnapshot.create(external_station_id=external_id, **values)
    return True


def record_piste_map_snapshot(stations, observed_at=None, complete=False):
    """Persist one parsed station/plan catalogue without preparing any media.

    A negative observation is authoritative only when the caller explicitly
    confirms that the complete catalogue passed all retrieval and structure
    checks. Positive observations remain useful even for an incomplete input.
    """
    observed_at = observed_at or utcnow()
    database = AnmsmStationSnapshot._meta.database
    if not database.table_exists(AnmsmStationSnapshot._meta.table_name):
        return False
    with database.atomic():
        for station in stations:
            maps = station.get("piste_maps") or []
            external_id = station["external_station_id"]
            row = AnmsmStationSnapshot.get_or_none(
                AnmsmStationSnapshot.external_station_id == external_id)
            values = {
                "station_name": station.get("external_name") or external_id,
                "piste_map_available": bool(maps) if maps or complete else None,
                "piste_map_url": maps[0].get("url") if maps else None,
                "piste_map_seen_at": observed_at,
                "piste_map_observation_complete": bool(complete),
                "station_catalog_seen_at": observed_at if complete else (
                    row.station_catalog_seen_at if row else None),
                "last_seen_at": observed_at,
            }
            if row:
                for key, value in values.items(): setattr(row, key, value)
                row.save()
            else:
                AnmsmStationSnapshot.create(external_station_id=external_id, **values)
    return True
