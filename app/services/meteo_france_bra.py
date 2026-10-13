"""DPBRA infrastructure. No active bulletin schema has been validated yet.

An asset is never evidence of a current danger level. No stale risk fallback.
"""
import hashlib
import json
import logging
import math
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict

import requests
from flask import current_app

from app.models.meteo_france_massif_override import MeteoFranceMassifOverride

BASE_URL = "https://public-api.meteofrance.fr/public/DPBRA/v1"
IMAGES = {
    "risk": "montagne-risques", "slopes": "rose-pentes",
    "snow-cover": "montagne-enneigement", "fresh-snow": "graphe-neige-fraiche",
    "weather": "apercu-meteo",
}
DEFAULTS = {
    "METEO_FRANCE_BRA_CONNECT_TIMEOUT": 3.05,
    "METEO_FRANCE_BRA_READ_TIMEOUT": 5,
    "METEO_FRANCE_BRA_MASSIFS_TTL": 86400,
    "METEO_FRANCE_BRA_XML_TTL": 300,
    "METEO_FRANCE_BRA_ASSETS_TTL": 300,
    "METEO_FRANCE_BRA_MAPPING_TTL": 86400,
}
LOGGER = logging.getLogger(__name__)


class BraError(Exception):
    """Only fixed error codes, never upstream bodies or exception messages."""


class TTLCache:
    """Bounded worker-local cache, with no stale reads or credential storage."""
    def __init__(self, max_entries=256, clock=time.monotonic, max_asset_bytes=16 * 1024 * 1024):
        self.entries = OrderedDict()
        self.lock = threading.RLock()
        self.max_entries = max_entries
        self.clock = clock
        self.max_asset_bytes = max_asset_bytes

    @staticmethod
    def _asset_size(item):
        value = item[1]
        return len(value[0]) if isinstance(value, tuple) and isinstance(value[0], bytes) else 0

    def get(self, key):
        with self.lock:
            item = self.entries.get(key)
            if item is None:
                return None
            if item[0] <= self.clock():
                del self.entries[key]
                return None
            self.entries.move_to_end(key)
            return item[1]

    def put(self, key, value, ttl):
        with self.lock:
            self.entries[key] = (self.clock() + ttl, value)
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
            while sum(self._asset_size(item) for item in self.entries.values()) > self.max_asset_bytes:
                self.entries.popitem(last=False)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse_massifs(content):
    try:
        data = json.loads(content)
        if data["type"] != "FeatureCollection" or not isinstance(data["features"], list) or not data["features"]:
            raise ValueError
        codes = set()
        for feature in data["features"]:
            p, g = feature["properties"], feature["geometry"]
            if (feature["type"] != "Feature" or type(p["code"]) is not int or p["code"] <= 0
                    or p["code"] in codes or g["type"] != "MultiPolygon"):
                raise ValueError
            codes.add(p["code"])
            for field in ("mountain", "title", "title_short", "Departement"):
                if not isinstance(p[field], str) or not p[field] or len(p[field]) > 255:
                    raise ValueError
            if p["Dep2"] is not None and not isinstance(p["Dep2"], str):
                raise ValueError
            if not finite(p["lon_center"]) or not finite(p["lat_center"]):
                raise ValueError
            if not -180 <= p["lon_center"] <= 180 or not -90 <= p["lat_center"] <= 90:
                raise ValueError
            if not g["coordinates"]:
                raise ValueError
            for polygon in g["coordinates"]:
                if not polygon:
                    raise ValueError
                for ring in polygon:
                    if len(ring) < 4 or ring[0] != ring[-1]:
                        raise ValueError
                    for point in ring:
                        if (len(point) != 2 or not all(finite(v) for v in point)
                                or not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90):
                            raise ValueError
        return data  # Preserve source property names and official titles verbatim.
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        raise BraError("invalid_massif_catalogue") from None


def ring_location(x, y, ring):
    """Return outside/inside/boundary; tolerance is ~sub-millimetre in degrees."""
    inside = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        length = math.hypot(bx - ax, by - ay)
        if (abs(cross) <= 1e-9 * length and min(ax, bx) - 1e-9 <= x <= max(ax, bx) + 1e-9
                and min(ay, by) - 1e-9 <= y <= max(ay, by) + 1e-9):
            return "boundary"
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return "inside" if inside else "outside"


def geometry_location(x, y, geometry):
    found = False
    for polygon in geometry["coordinates"]:
        locations = [ring_location(x, y, ring) for ring in polygon]
        if "boundary" in locations:
            return "boundary"
        if locations[0] == "inside" and all(v == "outside" for v in locations[1:]):
            found = True
    return "inside" if found else "outside"


def map_massif(catalogue, latitude, longitude, manual_id=None):
    def confirmed(feature, method):
        p = feature["properties"]
        return {"mapping_status": "confirmed", "id": p["code"], "name": p["title"],
                "mapping_method": method}
    if manual_id is not None:
        for feature in catalogue["features"]:
            if type(manual_id) is int and feature["properties"]["code"] == manual_id:
                return confirmed(feature, "manual")
        # Invalid override never silently falls back to automatic mapping.
        return {"mapping_status": "unmapped"}
    if (not finite(latitude) or not finite(longitude)
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180):
        return {"mapping_status": "unmapped"}
    matches, boundary = [], False
    for feature in catalogue["features"]:
        location = geometry_location(longitude, latitude, feature["geometry"])
        boundary |= location == "boundary"
        if location == "inside":
            matches.append(feature)
    if boundary or len(matches) > 1:
        return {"mapping_status": "ambiguous"}
    return confirmed(matches[0], "automatic") if matches else {"mapping_status": "unmapped"}


def bulletin_reason(content):
    """Recognize only the audited message. Everything else fails closed."""
    # Reject DTDs/entities, including UTF-16 encodings, before XML parsing.
    if b"<!DOCTYPE" in content.upper().replace(b"\x00", b"") or b"<!ENTITY" in content.upper().replace(b"\x00", b""):
        LOGGER.warning("DPBRA unsupported XML: declaration rejected bytes=%d", len(content))
        return "unsupported_bulletin_schema"
    try:
        root = ET.fromstring(content)
    except (ET.ParseError, ValueError):
        LOGGER.warning("DPBRA unsupported XML: malformed bytes=%d", len(content))
        return "unsupported_bulletin_schema"
    if root.tag == "message" and not root.attrib and len(root) == 0:
        message = (root.text or "").casefold()
        if "la saison est terminée" in message:
            return "season_ended"
        if "indisponible" in message or "aucun bulletin" in message:
            return "upstream_unavailable"
    # Structural diagnostics only: no text, attribute values, or arbitrary tags.
    LOGGER.warning("DPBRA unsupported XML: bytes=%d elements=%d message_root=%s root_attributes=%d",
                   len(content), sum(1 for _ in root.iter()), root.tag == "message", len(root.attrib))
    return "unsupported_bulletin_schema"


class BraService:
    def __init__(self, config=None, http_get=None, cache=None, override_lookup=None):
        self.config = current_app.config if config is None else config
        self.http_get = http_get or requests.get
        if cache is None and config is None:
            cache = current_app.extensions.setdefault("meteo_france_bra_cache", TTLCache())
        self.cache = cache if cache is not None else TTLCache()
        self.override_lookup = override_lookup or self._override

    def setting(self, name):
        return self.config.get(name, DEFAULTS[name])

    @staticmethod
    def _override(resort):
        row = MeteoFranceMassifOverride.get_or_none(MeteoFranceMassifOverride.resort == resort.id)
        return row.massif_id if row else None

    def _get(self, path, params, expected, limit):
        key = self.config.get("METEO_FRANCE_BRA_API_KEY")
        if not key:
            raise BraError("not_configured")
        try:
            with self.http_get(BASE_URL + path, params=params, headers={"apikey": key},
                               timeout=(self.setting("METEO_FRANCE_BRA_CONNECT_TIMEOUT"),
                                        self.setting("METEO_FRANCE_BRA_READ_TIMEOUT")),
                               allow_redirects=False, stream=True) as response:
                status = response.status_code
                if status != 200:
                    code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(status, "upstream_error")
                    LOGGER.warning("DPBRA request failed: status=%d code=%s", status, code)
                    raise BraError(code)
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if mime not in expected:
                    raise BraError("unexpected_content_type")
                body = bytearray()
                deadline = time.monotonic() + 15
                for chunk in response.iter_content(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > limit or time.monotonic() > deadline:
                        raise BraError("response_limit")
                content = bytes(body)
                # Never forward a response containing the configured credential.
                if key.encode() in content:
                    raise BraError("unsafe_response")
                return content, mime
        except requests.RequestException:
            LOGGER.warning("DPBRA transport unavailable")
            raise BraError("transport_error") from None

    def massifs(self):
        cached = self.cache.get("massifs")
        if cached is not None:
            return cached
        content, _ = self._get("/liste-massifs", None, {"application/geo+json", "application/json"}, 2 * 1024 * 1024)
        result = parse_massifs(content)
        if self.config["METEO_FRANCE_BRA_API_KEY"] in json.dumps(result, ensure_ascii=False):
            raise BraError("unsafe_response")
        self.cache.put("massifs", result, self.setting("METEO_FRANCE_BRA_MASSIFS_TTL"))
        return result

    def mapping(self, resort):
        catalogue = self.massifs()
        manual_id = self.override_lookup(resort)
        revision = hashlib.sha256(json.dumps(catalogue, sort_keys=True).encode()).hexdigest()
        key = ("mapping", resort.id, str(resort.latitude), str(resort.longitude), str(manual_id), revision)
        result = self.cache.get(key)
        if result is None:
            result = map_massif(catalogue, resort.latitude, resort.longitude, manual_id)
            self.cache.put(key, result, self.setting("METEO_FRANCE_BRA_MAPPING_TTL"))
        return result

    @staticmethod
    def _params(massif_id):
        if type(massif_id) is not int or massif_id <= 0:
            raise BraError("invalid_massif_id")
        return {"id-massif": massif_id}

    def xml_reason(self, massif_id):
        params = self._params(massif_id)
        key = ("xml", massif_id)
        reason = self.cache.get(key)
        if reason is None:
            try:
                content, _ = self._get("/massif/BRA", {**params, "format": "xml"}, {"text/xml", "application/xml"}, 512 * 1024)
                reason = bulletin_reason(content)
            except BraError:
                reason = "upstream_unavailable"
            self.cache.put(key, reason, self.setting("METEO_FRANCE_BRA_XML_TTL") if reason != "upstream_unavailable" else 30)
        return reason

    def asset(self, massif_id, kind):
        params = self._params(massif_id)
        if kind == "pdf":
            path, params, mime, signature = "/massif/BRA", {**params, "format": "pdf"}, "application/pdf", b"%PDF-"
        elif kind in IMAGES:
            path, mime, signature = "/massif/image/" + IMAGES[kind], "image/png", b"\x89PNG\r\n\x1a\n"
        else:
            raise BraError("invalid_asset")
        key = ("asset", massif_id, kind)
        cached = self.cache.get(key)
        if cached is not None:
            if isinstance(cached, BraError):
                raise BraError(str(cached))
            return cached
        try:
            content, received_mime = self._get(path, params, {mime}, 4 * 1024 * 1024)
            if not content.startswith(signature):
                raise BraError("invalid_asset_signature")
            result = content, received_mime
        except BraError as error:
            self.cache.put(key, BraError(str(error)), 30)
            raise
        self.cache.put(key, result, self.setting("METEO_FRANCE_BRA_ASSETS_TTL"))
        return result

    def asset_available(self, massif_id, kind):
        try:
            self.asset(massif_id, kind)
            return True
        except BraError as error:
            return False if str(error) == "not_found" else None

    def risk(self, resort):
        result = {
            "station": {"slug": resort.slug, "name": resort.name}, "status": "unavailable",
            "massif": None, "mapping_status": None,
            "risk": dict.fromkeys(("level", "label", "valid_from", "valid_until", "issued_at", "summary")),
            "source": {"provider": "Météo-France", "label": "Bulletin Risque Avalanche Météo-France", "scope": "massif"},
            "assets": {"bulletin_pdf_available": None, "risk_image_available": None, "current": False},
            "unavailable_reason": "upstream_unavailable",
            "display": {"unavailable_message": "Bulletin avalanche Météo-France actuellement indisponible pour ce massif."},
        }
        try:
            mapping = self.mapping(resort)
        except BraError:
            return result
        result["mapping_status"] = mapping["mapping_status"]
        if mapping["mapping_status"] != "confirmed":
            result["unavailable_reason"] = "ambiguous_massif" if mapping["mapping_status"] == "ambiguous" else "no_massif"
            return result
        result["massif"] = {k: mapping[k] for k in ("id", "name", "mapping_method")}
        name = mapping["name"]
        label = "du Mercantour" if name == "Mercantour" else f"« {name} »"
        result["display"].update({
            "title": f"Risque d’avalanche — massif {label}",
            "disclaimer": f"Ce niveau de danger concerne le massif {label}. Il ne constitue pas une évaluation du risque propre à la station, aux pistes sécurisées ou à un itinéraire précis. Les conditions peuvent varier localement.",
            "bulletin_link_label": "Consulter le bulletin complet Météo-France",
        })
        result["unavailable_reason"] = self.xml_reason(mapping["id"])
        result["assets"]["bulletin_pdf_available"] = self.asset_available(mapping["id"], "pdf")
        result["assets"]["risk_image_available"] = self.asset_available(mapping["id"], "risk")
        return result
