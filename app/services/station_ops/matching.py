"""Conservative, factual matching on shared in-memory identity indexes."""
from collections import defaultdict
from itertools import product
import math

from .duplicates import DEFAULT_DISTANCE_M, EARTH_RADIUS_M, normalized_name
from .normalization import normalize

IDENTITY_FIELDS = ("id", "slug", "name", "country_code", "region_id", "department", "latitude", "longitude")
FAR_REVIEW_M = 20000  # Widely separated points demand review, not a silent identity choice.


def station_reference(row):
    return {field: row.get(field) for field in ("id", "slug", "name")}


def _point(row):
    if row.get("latitude") is None or row.get("longitude") is None:
        return None
    lat, lon = math.radians(float(row["latitude"])), math.radians(float(row["longitude"]))
    return (EARTH_RADIUS_M * math.cos(lat) * math.cos(lon),
            EARTH_RADIUS_M * math.cos(lat) * math.sin(lon), EARTH_RADIUS_M * math.sin(lat))


def distance_m(left, right):
    a, b = _point(left), _point(right)
    if a is None or b is None:
        return None
    return 2 * EARTH_RADIUS_M * math.asin(min(1, math.dist(a, b) / (2 * EARTH_RADIUS_M)))


class MatchingIndex:
    def __init__(self, rows):
        self.rows = {}
        self.normalized = {}
        self.slugs, self.names, self.buckets = defaultdict(list), defaultdict(list), defaultdict(list)
        self.chord = 2 * EARTH_RADIUS_M * math.sin(DEFAULT_DISTANCE_M / (2 * EARTH_RADIUS_M))
        for row in rows:
            self.rows[row["id"]] = row
            norm = {}
            for field in IDENTITY_FIELDS:
                try:
                    norm[field] = normalize(field, row.get(field))
                except (ValueError, TypeError, UnicodeError, OverflowError):
                    norm[field] = None
            self.normalized[row["id"]] = norm
            if norm.get("slug"):
                self.slugs[norm["slug"]].append(row["id"])
            if normalized_name(norm.get("name")):
                self.names[normalized_name(norm["name"])].append(row["id"])
            point = _point(norm)
            if point:
                self.buckets[tuple(math.floor(axis / self.chord) for axis in point)].append((row["id"], point))

    def _nearby(self, norm):
        point = _point(norm)
        if point is None:
            return []
        cell = tuple(math.floor(axis / self.chord) for axis in point)
        return [identity for offset in product((-1, 0, 1), repeat=3)
                for identity, other in self.buckets.get(tuple(cell[i] + offset[i] for i in range(3)), ())
                if math.dist(point, other) <= self.chord]

    def _evidence(self, identity, candidate):
        existing = self.normalized[identity]
        reasons, conflicts = [], []
        if candidate.get("id") == existing["id"]:
            reasons.append("exact_id")
        if candidate.get("slug") and candidate["slug"] == existing["slug"]:
            reasons.append("exact_slug")
        if normalized_name(candidate.get("name")) and normalized_name(candidate["name"]) == normalized_name(existing["name"]):
            reasons.append("exact_normalized_name")
        for field, reason in (("country_code", "same_country"), ("region_id", "same_region"),
                              ("department", "same_department")):
            if candidate.get(field) and existing.get(field):
                (reasons if candidate[field] == existing[field] else conflicts).append(
                    reason if candidate[field] == existing[field] else field)
        distance = distance_m(candidate, existing)
        if distance is not None and distance <= DEFAULT_DISTANCE_M:
            reasons.append("coordinates_nearby")
        elif distance is not None and distance > FAR_REVIEW_M:
            conflicts.append("coordinates_far_apart")
        return reasons, conflicts, distance

    def match(self, candidate):
        exact_id = candidate.get("id")
        if exact_id in self.rows:
            # Exact ID is authoritative unless another explicit identifier points
            # to a different existing row. Geographic edits remain valid diffs.
            slug_ids = self.slugs.get(candidate.get("slug"), [])
            if any(identity != exact_id for identity in slug_ids):
                return self._review(sorted(set([exact_id, *slug_ids])), candidate, "conflicting_station_identifiers")
            reasons, _, distance = self._evidence(exact_id, candidate)
            return {"station": self.rows[exact_id], "reasons": reasons, "review_items": [], "distance_m": distance}
        slug_ids = self.slugs.get(candidate.get("slug"), [])
        if slug_ids:
            plausible = sorted(slug_ids)
        else:
            plausible = []
            for identity in self.names.get(normalized_name(candidate.get("name")), []):
                reasons, conflicts, _ = self._evidence(identity, candidate)
                # A supplied geographic context can separate homonymous resorts.
                if not any(field in conflicts for field in ("country_code", "region_id", "department")) or "coordinates_nearby" in reasons:
                    plausible.append(identity)
            plausible.sort()
        if plausible:
            if len(plausible) != 1:
                return self._review(plausible, candidate, "multiple_station_matches")
            identity = plausible[0]
            reasons, conflicts, distance = self._evidence(identity, candidate)
            safe = ("exact_slug" in reasons and ("same_country" in reasons or "coordinates_nearby" in reasons)) or (
                "exact_normalized_name" in reasons and ("coordinates_nearby" in reasons or
                ("same_country" in reasons and ("same_region" in reasons or "same_department" in reasons))))
            if conflicts or not safe or exact_id:
                code = "geographic_context_conflict" if conflicts else (
                    "candidate_id_not_found" if exact_id else "insufficient_matching_evidence")
                return self._review(plausible, candidate, code)
            return {"station": self.rows[identity], "reasons": reasons, "review_items": [], "distance_m": distance}
        nearby = [identity for identity in self._nearby(candidate)
                  if "country_code" not in self._evidence(identity, candidate)[1]]
        if nearby:
            return self._review(sorted(nearby), candidate, "coordinates_only_match")
        return {"station": None, "reasons": [], "review_items": [], "distance_m": None}

    def _review(self, identities, candidate, code):
        options = []
        for identity in identities:
            reasons, conflicts, distance = self._evidence(identity, candidate)
            options.append({**station_reference(self.rows[identity]), "match_reasons": reasons,
                            "conflicting_fields": conflicts,
                            **({"distance_m": round(distance, 2)} if distance is not None else {})})
        reasons = sorted({reason for option in options for reason in option["match_reasons"]})
        if len(options) > 1:
            reasons.append("multiple_matches")
        return {"station": None, "reasons": reasons, "review_items": [{"code": code, "candidates": options}], "distance_m": None}
