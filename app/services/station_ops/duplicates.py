"""Pure candidate detection; no station pair is classified as certain."""
from collections import defaultdict
from itertools import combinations, product
import math
import unicodedata

from .validation import valid_coordinates

EARTH_RADIUS_M = 6371008.8
DEFAULT_DISTANCE_M = 150


def normalized_name(value):
    text = unicodedata.normalize("NFKD", value or "").casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def potential_duplicates(stations, *, distance_m=DEFAULT_DISTANCE_M):
    if not math.isfinite(distance_m) or not 0 < distance_m < math.pi * EARTH_RADIUS_M:
        raise ValueError("distance_m must be positive and less than half the Earth's circumference")
    rows = sorted(stations, key=lambda row: row["id"])
    reasons = defaultdict(set)
    distances = {}
    for field, normalize, reason in (("slug", lambda v: v, "same_slug"),
                                      ("name", normalized_name, "same_normalized_name")):
        groups = defaultdict(list)
        for row in rows:
            value = normalize(row.get(field))
            if value and value.strip():
                groups[value].append(row["id"])
        for ids in groups.values():
            for pair in combinations(ids, 2):
                reasons[pair].add(reason)
    # 3D chord buckets cover poles and the antimeridian without O(n²) global scans.
    chord = 2 * EARTH_RADIUS_M * math.sin(distance_m / (2 * EARTH_RADIUS_M))
    buckets = defaultdict(list)
    for row in rows:
        if not valid_coordinates(row):
            continue
        lat, lon = math.radians(row["latitude"]), math.radians(row["longitude"])
        point = (EARTH_RADIUS_M * math.cos(lat) * math.cos(lon),
                 EARTH_RADIUS_M * math.cos(lat) * math.sin(lon), EARTH_RADIUS_M * math.sin(lat))
        cell = tuple(math.floor(axis / chord) for axis in point)
        for offset in product((-1, 0, 1), repeat=3):
            neighbour = tuple(cell[i] + offset[i] for i in range(3))
            for previous_id, previous in buckets.get(neighbour, ()):
                length = math.dist(point, previous)
                if length <= chord:
                    pair = (previous_id, row["id"])
                    reasons[pair].add("near_coordinates")
                    distances[pair] = round(2 * EARTH_RADIUS_M * math.asin(
                        min(1, length / (2 * EARTH_RADIUS_M))), 2)
        buckets[cell].append((row["id"], point))
    return [{"station_ids": list(pair), "classification": "potential_duplicates",
             "reasons": sorted(codes), **({"distance_m": distances[pair]} if pair in distances else {})}
            for pair, codes in sorted(reasons.items())]
