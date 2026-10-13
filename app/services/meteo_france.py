"""DPObs v2 observations. Credentials and upstream error bodies never leave here."""
import csv
import io
import json
import math
import re
from datetime import datetime, timezone

import requests
from flask import current_app
from peewee import DatabaseError

from app.datetime_utils import utcnow
from app.models.meteo_france_station_mapping import MeteoFranceStationMapping

BASE_URL = "https://public-api.meteofrance.fr/public/DPObs/v2"
STATIONS_KEY = "snow:meteo-france:stations"
STATION_ID = re.compile(r"^[0-9]{8}$")
NUMERIC_FIELDS = ("sss", "t", "u", "dd", "ff", "etat_sol", "rr_per", "rr1")
TIME_FIELDS = ("validity_time", "reference_time", "insert_time")
DEFAULTS = {
    "METEO_FRANCE_CONNECT_TIMEOUT": 3.05,
    "METEO_FRANCE_READ_TIMEOUT": 5,
    "METEO_FRANCE_STATIONS_CACHE_TTL_SECONDS": 86400,
    "METEO_FRANCE_OBSERVATION_CACHE_TTL_SECONDS": 300,
    "METEO_FRANCE_STALE_CACHE_TTL_SECONDS": 604800,
    "METEO_FRANCE_FRESHNESS_THRESHOLD_MINUTES": 120,
    "METEO_FRANCE_MAX_CANDIDATES": 5,
    "METEO_FRANCE_MAX_DISTANCE_KM": 75,
}
VALIDATED_MAPPINGS = {
    "la-colmiane": {"id": "06153400", "name": "MILLEFONTS-NIVOSE",
                    "latitude": 44.118333, "longitude": 7.192, "altitude_m": 2430},
    "luz-ardiden": {"id": "65413400", "name": "LAC D'ARDIDEN-NIVOSE",
                    "latitude": 42.848167, "longitude": -0.066, "altitude_m": 2445},
    "val-thorens": {"id": "73257005", "name": "VAL THORENS_SAPC",
                    "latitude": 45.294, "longitude": 6.581, "altitude_m": 2277},
}


class MeteoFranceError(Exception):
    """Safe, bounded error codes only; never interpolate upstream exceptions."""


def number(value):
    return value if (isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value)) else None


def snow_cm(value):
    value = number(value)
    return round(value * 100, 4) if value is not None else None


def temperature_c(value):
    value = number(value)
    return round(value - 273.15, 2) if value is not None else None


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def haversine(lat1, lon1, lat2, lon2):
    a, b, c, d = map(math.radians, (lat1, lon1, lat2, lon2))
    h = math.sin((c - a) / 2) ** 2 + math.cos(a) * math.cos(c) * math.sin((d - b) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def parse_stations(content):
    """The station catalogue is Latin-1 CSV, not UTF-8 or JSON."""
    reader = csv.DictReader(io.StringIO(content.decode("iso-8859-1")), delimiter=";")
    required = {"Id_station", "Nom_usuel", "Latitude", "Longitude", "Altitude"}
    if not required.issubset(reader.fieldnames or []):
        raise MeteoFranceError("invalid_station_catalogue")
    stations = []
    for row in reader:
        sid = row["Id_station"] or ""
        if not STATION_ID.fullmatch(sid):
            continue
        try:
            lat, lon, altitude = float(row["Latitude"]), float(row["Longitude"]), int(row["Altitude"])
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        stations.append({"id": sid, "name": row["Nom_usuel"], "latitude": lat,
                         "longitude": lon, "altitude_m": altitude})
    if not stations:
        raise MeteoFranceError("empty_station_catalogue")
    return stations


def clean_record(record, station_id):
    """Allowlist typed fields; never forward unknown upstream data or secrets."""
    if not isinstance(record, dict) or str(record.get("geo_id_insee")) != station_id:
        return None
    measured = timestamp(record.get("validity_time"))
    if measured is None or not any(number(record.get(f)) is not None for f in NUMERIC_FIELDS):
        return None
    result = {"geo_id_insee": station_id}
    for field in NUMERIC_FIELDS:
        if field in record:
            result[field] = number(record[field])
    for field in TIME_FIELDS:
        if field in record:
            parsed = timestamp(record[field])
            result[field] = parsed.isoformat().replace("+00:00", "Z") if parsed else None
    return result


class MeteoFranceService:
    def __init__(self, config=None, cache=None, http_get=None, now=None):
        self.config = current_app.config if config is None else config
        self.http_get = http_get or requests.get
        self.now = now or utcnow
        self._observations = {}
        self.cache = cache
        if cache is None and config is None:
            self.cache = current_app.extensions.get("public_cache_redis")
            if self.cache is None and self.config.get("REDIS_URL"):
                if "meteo_france_redis" not in current_app.extensions:
                    import redis
                    current_app.extensions["meteo_france_redis"] = redis.Redis.from_url(
                        self.config["REDIS_URL"], socket_connect_timeout=0.15,
                        socket_timeout=0.2, retry_on_timeout=False)
                self.cache = current_app.extensions["meteo_france_redis"]

    def setting(self, key):
        return self.config.get(key, DEFAULTS[key])

    def _get(self, path, params=None):
        key = self.config.get("METEO_FRANCE_API_KEY")
        if not key:
            raise MeteoFranceError("not_configured")
        try:
            response = self.http_get(
                BASE_URL + path, params=params,
                headers={"accept": "*/*", "apikey": key},
                timeout=(self.setting("METEO_FRANCE_CONNECT_TIMEOUT"),
                         self.setting("METEO_FRANCE_READ_TIMEOUT")),
                allow_redirects=False)
            if response.status_code != 200:
                response.close()
                raise MeteoFranceError("upstream_unavailable")
            return response
        except requests.RequestException:
            raise MeteoFranceError("upstream_unavailable") from None

    def _cached(self, key):
        if self.cache is None:
            return None
        try:
            raw = self.cache.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            # No exception text: Redis connection errors can contain credentials.
            return None

    def _store(self, key, data, ttl):
        if self.cache is not None:
            try:
                self.cache.setex(key, max(1, int(ttl)), json.dumps(data, allow_nan=False))
            except Exception:
                pass

    def stations(self):
        cached = self._cached(STATIONS_KEY)
        if self._valid_catalogue(cached):
            return cached
        try:
            response = self._get("/liste-stations")
            try:
                stations = parse_stations(response.content)
            finally:
                response.close()
            secret = self.config.get("METEO_FRANCE_API_KEY")
            for station in stations:
                if secret:
                    station["name"] = station["name"].replace(secret, "[REDACTED]")
        except MeteoFranceError:
            last = self._cached(STATIONS_KEY + ":last-good")
            if self._valid_catalogue(last):
                return last
            raise
        self._store(STATIONS_KEY, stations, self.setting("METEO_FRANCE_STATIONS_CACHE_TTL_SECONDS"))
        self._store(STATIONS_KEY + ":last-good", stations, self.setting("METEO_FRANCE_STALE_CACHE_TTL_SECONDS"))
        return stations

    @staticmethod
    def _valid_catalogue(data):
        return isinstance(data, list) and bool(data) and all(
            isinstance(s, dict) and STATION_ID.fullmatch(str(s.get("id", "")))
            and isinstance(s.get("name"), str)
            and number(s.get("latitude")) is not None and -90 <= s["latitude"] <= 90
            and number(s.get("longitude")) is not None and -180 <= s["longitude"] <= 180
            and number(s.get("altitude_m")) is not None for s in data)

    def _age(self, record):
        measured = timestamp(record.get("validity_time"))
        return (self.now() - measured).total_seconds() / 60 if measured else None

    def _fresh(self, record):
        age = self._age(record)
        return age is not None and 0 <= age <= self.setting("METEO_FRANCE_FRESHNESS_THRESHOLD_MINUTES")

    def _fetch_observation(self, sid, endpoint):
        response = self._get("/station/" + endpoint, {"id_station": sid, "format": "json"})
        try:
            data = response.json()
        except ValueError:
            raise MeteoFranceError("invalid_observation") from None
        finally:
            response.close()
        if not isinstance(data, list):
            raise MeteoFranceError("invalid_observation")
        records = [clean_record(r, sid) for r in data]
        records = [r for r in records if r is not None]
        if not records:
            return None
        return {"record": max(records, key=lambda r: timestamp(r["validity_time"])),
                "endpoint": endpoint}

    def _valid_observation_cache(self, value, sid):
        if not isinstance(value, dict) or value.get("endpoint") not in {"horaire", "infrahoraire-6m"}:
            return None
        record = clean_record(value.get("record"), sid)
        return dict(record=record, endpoint=value["endpoint"]) if record else None

    def observation(self, sid):
        if not isinstance(sid, str) or not STATION_ID.fullmatch(sid):
            raise MeteoFranceError("invalid_station_id")
        if sid in self._observations:
            return self._observations[sid]
        key = "snow:meteo-france:observation:" + sid
        cached = self._valid_observation_cache(self._cached(key), sid)
        if cached:
            self._observations[sid] = dict(cached, cache_fallback=False)
            return self._observations[sid]
        options = []
        for endpoint in ("infrahoraire-6m", "horaire"):
            try:
                result = self._fetch_observation(sid, endpoint)
            except MeteoFranceError:
                result = None
            if result:
                options.append(result)
                if self._fresh(result["record"]):
                    break
        if options:
            result = max(options, key=lambda o: timestamp(o["record"]["validity_time"]))
            self._store(key, result, self.setting("METEO_FRANCE_OBSERVATION_CACHE_TTL_SECONDS"))
            self._store(key + ":last-good", result, self.setting("METEO_FRANCE_STALE_CACHE_TTL_SECONDS"))
            self._observations[sid] = dict(result, cache_fallback=False)
            return self._observations[sid]
        last = self._valid_observation_cache(self._cached(key + ":last-good"), sid)
        if last:
            self._observations[sid] = dict(last, cache_fallback=True)
            return self._observations[sid]
        raise MeteoFranceError("observation_unavailable")

    @staticmethod
    def _distance(resort, station):
        lat, lon = number(resort.latitude), number(resort.longitude)
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return haversine(lat, lon, station["latitude"], station["longitude"])

    @staticmethod
    def _altitude_gap(resort, station):
        low = resort.altitude_min_m if resort.altitude_min_m is not None else resort.altitude_base_m
        high = resort.altitude_max_m if resort.altitude_max_m is not None else resort.altitude_top_m
        return max(0, (low - station["altitude_m"]) if low is not None else 0,
                   (station["altitude_m"] - high) if high is not None else 0)

    def _persist(self, resort, station, manual):
        distance = self._distance(resort, station)
        values = dict(meteo_france_station_id=station["id"], station_name=station["name"],
                      latitude=station["latitude"], longitude=station["longitude"],
                      altitude_m=station["altitude_m"], distance_km=distance,
                      is_manual_override=manual, updated_at=utcnow())
        try:
            MeteoFranceStationMapping.insert(resort=resort.id, **values).on_conflict(
                conflict_target=[MeteoFranceStationMapping.resort], update=values,
                where=(MeteoFranceStationMapping.is_manual_override == False)).execute()
        except DatabaseError:
            # During rollout a missing mapping table must not break weather.
            pass
        return dict(station, distance_km=distance, is_manual_override=manual)

    def set_manual_mapping(self, resort, sid):
        """Operator entry point; validates against the catalogue before saving."""
        station = next((s for s in self.stations() if s["id"] == sid), None)
        if station is None:
            raise MeteoFranceError("unknown_station_id")
        values = dict(meteo_france_station_id=sid, station_name=station["name"],
                      latitude=station["latitude"], longitude=station["longitude"],
                      altitude_m=station["altitude_m"], distance_km=self._distance(resort, station),
                      is_manual_override=True, updated_at=utcnow())
        return MeteoFranceStationMapping.insert(resort=resort.id, **values).on_conflict(
            conflict_target=[MeteoFranceStationMapping.resort], update=values).execute()

    def mapping(self, resort):
        try:
            saved = MeteoFranceStationMapping.get_or_none(MeteoFranceStationMapping.resort == resort.id)
        except DatabaseError:
            saved = None
        if saved and saved.is_manual_override:
            station = dict(id=saved.meteo_france_station_id, name=saved.station_name,
                           latitude=saved.latitude, longitude=saved.longitude, altitude_m=saved.altitude_m)
            return dict(station, distance_km=self._distance(resort, station), is_manual_override=True)
        if resort.slug in VALIDATED_MAPPINGS:
            return self._persist(resort, VALIDATED_MAPPINGS[resort.slug], True)
        if saved:
            # Reuse automatic mappings while their observations remain useful.
            station = dict(id=saved.meteo_france_station_id, name=saved.station_name,
                           latitude=saved.latitude, longitude=saved.longitude, altitude_m=saved.altitude_m)
            try:
                observation = self.observation(station["id"])
                if (self._fresh(observation["record"]) and not observation["cache_fallback"]
                        and number(observation["record"].get("sss")) is not None):
                    return dict(station, distance_km=self._distance(resort, station), is_manual_override=False)
            except MeteoFranceError:
                pass
        candidates = []
        for station in self.stations():
            distance = self._distance(resort, station)
            if distance is not None and distance <= self.setting("METEO_FRANCE_MAX_DISTANCE_KM"):
                gap = self._altitude_gap(resort, station)
                candidates.append((distance + gap / 100, station["id"], station, distance, gap))
        candidates.sort(key=lambda c: c[:2])
        ranked = []
        for score, sid, station, distance, gap in candidates[:self.setting("METEO_FRANCE_MAX_CANDIDATES")]:
            try:
                result = self.observation(sid)
            except MeteoFranceError:
                continue
            record = result["record"]
            age = self._age(record)
            rank = (not self._fresh(record) or result["cache_fallback"],
                    number(record.get("sss")) is None, score, max(0, age), sid)
            ranked.append((rank, station))
        if not ranked:
            if saved:
                return dict(id=saved.meteo_france_station_id, name=saved.station_name,
                            latitude=saved.latitude, longitude=saved.longitude,
                            altitude_m=saved.altitude_m, distance_km=saved.distance_km,
                            is_manual_override=False)
            raise MeteoFranceError("mapping_unavailable")
        return self._persist(resort, min(ranked, key=lambda r: r[0])[1], False)

    def weather(self, resort):
        payload = {"station": {"slug": resort.slug, "name": resort.name},
                   "status": "unavailable", "observation": None, "source": None}
        try:
            station = self.mapping(resort)
            payload["source"] = {
                "provider": "Météo-France", "station_id": station["id"],
                "station_name": station["name"], "station_altitude_m": station["altitude_m"],
                "distance_km": round(station["distance_km"], 3) if station["distance_km"] is not None else None,
                "is_manual_override": station["is_manual_override"],
                "endpoint": None, "cache_fallback": False,
            }
            result = self.observation(station["id"])
        except MeteoFranceError:
            return payload
        record = result["record"]
        fresh = self._fresh(record) and not result["cache_fallback"]
        payload["status"] = "available" if fresh else "stale"
        payload["source"].update(endpoint=result["endpoint"], cache_fallback=result["cache_fallback"])
        precipitation_field = "rr_per" if result["endpoint"] == "infrahoraire-6m" else "rr1"
        payload["observation"] = {
            "temperature_c": temperature_c(record.get("t")),
            "humidity_percent": number(record.get("u")),
            "wind_direction_deg": number(record.get("dd")),
            "wind_speed": number(record.get("ff")), "wind_speed_unit": None,
            "precipitation": number(record.get(precipitation_field)), "precipitation_unit": None,
            "precipitation_period_minutes": 6 if result["endpoint"] == "infrahoraire-6m" else 60,
            "snow_depth_cm": snow_cm(record.get("sss")), "ground_state": number(record.get("etat_sol")),
            **{f: record.get(f) for f in TIME_FIELDS},
            "age_minutes": round(self._age(record), 2), "fresh": fresh,
            "raw": {f: record.get(f) for f in NUMERIC_FIELDS},
            "field_presence": {f: ("absent" if f not in record else "null" if record[f] is None else "value")
                               for f in NUMERIC_FIELDS + TIME_FIELDS},
        }
        return payload
