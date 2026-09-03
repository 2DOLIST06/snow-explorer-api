"""Read-only matching suggestions and explicit ANMSM mapping validation."""
import re
import unicodedata
from difflib import SequenceMatcher

from app.datetime_utils import utcnow
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.resort import Resort


def normalize_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def resort_json(resort):
    return {"station_id": resort.id, "name": resort.name, "slug": resort.slug}


def suggestions(external_name, resorts, limit=5):
    needle = normalize_name(external_name)
    if not needle:
        return []
    ranked = []
    for resort in resorts:
        candidate = normalize_name(resort.name)
        if not candidate:
            continue
        exact = needle == candidate
        score = 100 if exact else round(SequenceMatcher(None, needle, candidate).ratio() * 100)
        # Avoid presenting unrelated resorts as useful candidates.
        if exact or score >= 45:
            ranked.append((exact, score, resort))
    ranked.sort(key=lambda item: (-item[0], -item[1], normalize_name(item[2].name), str(item[2].id)))
    return [{**resort_json(resort), "score": score,
             "match_type": "normalized_exact" if exact else "similar"}
            for exact, score, resort in ranked[:limit]]


def mapping_json(mapping):
    if not mapping or not mapping.station_id:
        return None
    return {**resort_json(mapping.station), "verified": bool(mapping.verified)}


def confirm_mappings(rows, valid_external_ids):
    """Validate and persist a batch in one database transaction."""
    with AnmsmStationMapping._meta.database.atomic():
        return _confirm_mappings(rows, valid_external_ids)


def _confirm_mappings(rows, valid_external_ids):
    results, prepared = [], []
    seen_external, seen_stations = set(), set()
    for index, row in enumerate(rows):
        external_id = row["external_station_id"].strip()
        station_id = row["station_id"].strip()
        error = None
        station = Resort.get_or_none(Resort.id == station_id) if station_id else None
        if external_id not in valid_external_ids:
            error = "unknown_external_station"
        elif not station:
            error = "unknown_station"
        elif external_id in seen_external or station_id in seen_stations:
            error = "duplicate_in_request"
        else:
            conflict = (AnmsmStationMapping.select().where(
                (AnmsmStationMapping.source == "anmsm") &
                (AnmsmStationMapping.station == station_id) &
                (AnmsmStationMapping.external_station_id != external_id)).first())
            if conflict:
                error = "station_already_mapped"
        result = {"index": index, "external_station_id": external_id, "station_id": station_id}
        if error:
            result.update(ok=False, error=error)
        else:
            seen_external.add(external_id); seen_stations.add(station_id)
            prepared.append((result, station))
        results.append(result)
    for result, station in prepared:
        mapping, _ = AnmsmStationMapping.get_or_create(
            source="anmsm", external_station_id=result["external_station_id"])
        mapping.station = station; mapping.source = "anmsm"
        mapping.verified = True; mapping.updated_at = utcnow(); mapping.save()
        result["ok"] = True
    return results
