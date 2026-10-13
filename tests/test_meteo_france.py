import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests
from flask import Flask
from peewee import SqliteDatabase

from app import create_app
from app.cli import register_admin_commands
from app.models.meteo_france_station_mapping import MeteoFranceStationMapping
from app.models.region import Region
from app.models.resort import Resort
from app.routes.station_weather import bp_station_weather
from app.services.meteo_france import (
    BASE_URL, DEFAULTS, STATIONS_KEY, VALIDATED_MAPPINGS, MeteoFranceError,
    MeteoFranceService, haversine, parse_stations, snow_cm, temperature_c,
)

NOW = datetime(2026, 10, 13, 11, 5, tzinfo=timezone.utc)
KEY = "unit-test-api-key"


class Redis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        self.ttls[key] = ttl


def response(data=None, code=200, content=b""):
    r = Mock(status_code=code, content=content)
    r.json.return_value = data
    return r


def record(sid="73257005", **changes):
    result = {
        "geo_id_insee": sid, "sss": 0.02, "t": 283.55,
        "u": None, "dd": None, "ff": None, "rr_per": 0.0, "etat_sol": None,
        "validity_time": "2026-10-13T11:00:00Z",
        "reference_time": "2026-10-13T11:03:07Z",
        "insert_time": "2026-10-13T11:01:41Z",
    }
    result.update(changes)
    return result


def service(http=None, cache=None, now=None, **config):
    return MeteoFranceService(config={"METEO_FRANCE_API_KEY": KEY, **config},
                              http_get=http or Mock(return_value=response([record()])),
                              cache=cache, now=now or (lambda: NOW))


@pytest.fixture
def database():
    database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
    models = [Region, Resort, MeteoFranceStationMapping]
    with database.bind_ctx(models, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(models)
        yield database
        database.close()


def resort(slug="val-thorens", **fields):
    return Resort.create(id=slug, name=slug, slug=slug, latitude=45.29777,
                         longitude=6.58377, altitude_min_m=1825, altitude_max_m=3230, **fields)


def csv_content():
    return ("Id_station;Id_omm;Nom_usuel;Latitude;Longitude;Altitude;Date_ouverture;Pack\n"
            "06153400;;POSTE ÉTENDU;44.118333;7.192;2430;2007-09-26;ETENDU\n").encode("iso-8859-1")


def test_csv_latin1_and_leading_zero():
    stations = parse_stations(csv_content())
    assert stations == [{"id": "06153400", "name": "POSTE ÉTENDU", "latitude": 44.118333,
                         "longitude": 7.192, "altitude_m": 2430}]


@pytest.mark.parametrize("content", [b"bad", b"Id_station;Latitude\n", csv_content().split(b"\n")[0]])
def test_bad_catalogue_rejected(content):
    with pytest.raises(MeteoFranceError):
        parse_stations(content)


def test_haversine():
    assert haversine(0, 0, 0, 0) == 0
    assert haversine(45.29777, 6.58377, 45.294, 6.581) == pytest.approx(0.472, abs=0.001)
    assert haversine(0, 0, 0, 180) == pytest.approx(20015.09, abs=0.01)


@pytest.mark.parametrize("raw,cm", [(0, 0), (0.05, 5), (0.02, 2), (None, None), ("0", None), (True, None)])
def test_snow_conversion(raw, cm):
    assert snow_cm(raw) == cm


@pytest.mark.parametrize("raw,c", [(283.55, 10.4), (273.15, 0), (260.15, -13), (None, None), (float("nan"), None)])
def test_temperature_conversion(raw, c):
    assert temperature_c(raw) == c


def test_authentication_timeout_and_no_redirect():
    http = Mock(return_value=response([record()]))
    service(http).observation("73257005")
    http.assert_called_once_with(BASE_URL + "/station/infrahoraire-6m",
                                params={"id_station": "73257005", "format": "json"},
                                headers={"accept": "*/*", "apikey": KEY},
                                timeout=(3.05, 5), allow_redirects=False)


def test_missing_key_never_calls_upstream():
    http = Mock()
    with pytest.raises(MeteoFranceError):
        service(http, METEO_FRANCE_API_KEY=None).observation("73257005")
    http.assert_not_called()


@pytest.mark.parametrize("unusable", [[], [{"geo_id_insee": "73257005", "t": None}],
                                       [record(validity_time=None)], [record(sid="06153400")]])
def test_fallback_to_hourly(unusable):
    hourly = record(rr1=1.2)
    hourly.pop("rr_per")
    http = Mock(side_effect=[response(unusable), response([hourly])])
    result = service(http).observation("73257005")
    assert result["endpoint"] == "horaire"
    assert len(http.call_args_list) == 2
    assert "rr_per" not in result["record"]


def test_error_6m_can_fall_back_to_hourly():
    http = Mock(side_effect=[response(code=503), response([record()])])
    assert service(http).observation("73257005")["endpoint"] == "horaire"


def test_stale_6m_tries_and_selects_newer_hourly():
    http = Mock(side_effect=[response([record(validity_time="2026-10-13T00:00:00Z")]),
                             response([record()])])
    assert service(http).observation("73257005")["endpoint"] == "horaire"


def test_selects_latest_valid_observation():
    http = Mock(return_value=response([record(t=270, validity_time="2026-10-13T10:00:00Z"),
                                      record(t=283.55), record(validity_time="broken")]))
    assert service(http).observation("73257005")["record"]["t"] == 283.55


def test_redis_cache_ttls_and_hit():
    cache = Redis()
    http = Mock(return_value=response([record()]))
    first = service(http, cache).observation("73257005")
    second = service(http, cache).observation("73257005")
    assert first == second
    assert http.call_count == 1
    assert cache.ttls["snow:meteo-france:observation:73257005"] == 300
    assert cache.ttls["snow:meteo-france:observation:73257005:last-good"] == 604800


def test_catalogue_cache_and_outage_fallback():
    cache = Redis()
    http = Mock(return_value=response(content=csv_content()))
    stations = service(http, cache).stations()
    assert service(http, cache).stations() == stations
    assert http.call_count == 1
    assert cache.ttls[STATIONS_KEY] == 86400
    del cache.data[STATIONS_KEY]
    http.side_effect = requests.Timeout(KEY)
    assert service(http, cache).stations() == stations


def test_redis_failure_does_not_block_api():
    cache = Mock()
    cache.get.side_effect = RuntimeError("Redis unavailable")
    cache.setex.side_effect = RuntimeError("Redis unavailable")
    assert service(cache=cache).observation("73257005")["record"]["sss"] == 0.02


def test_corrupt_cache_is_ignored():
    cache = Redis()
    cache.data["snow:meteo-france:observation:73257005"] = json.dumps({"record": "bad"})
    cache.data[STATIONS_KEY] = "broken JSON"
    http = Mock(return_value=response([record()]))
    assert service(http, cache).observation("73257005")["record"]["sss"] == 0.02
    assert http.call_count == 1


def test_api_error_is_safe_and_no_secret_in_logs(caplog):
    http = Mock(side_effect=requests.RequestException("upstream echoed " + KEY))
    with pytest.raises(MeteoFranceError) as error:
        service(http).observation("73257005")
    assert KEY not in str(error.value)
    assert KEY not in caplog.text


def test_last_good_cache_forces_stale_even_when_measurement_is_recent(database):
    cache = Redis()
    r = resort()
    service(cache=cache).weather(r)
    del cache.data["snow:meteo-france:observation:73257005"]
    http = Mock(side_effect=requests.Timeout(KEY))
    payload = service(http, cache).weather(r)
    assert payload["status"] == "stale"
    assert payload["observation"]["fresh"] is False
    assert payload["source"]["cache_fallback"] is True
    assert payload["observation"]["snow_depth_cm"] == 2


def test_age_recomputed_on_cached_observations(database):
    cache = Redis()
    r = resort()
    service(cache=cache).weather(r)
    payload = service(cache=cache, now=lambda: NOW + timedelta(hours=3)).weather(r)
    assert payload["status"] == "stale"
    assert payload["observation"]["age_minutes"] == 185
    assert payload["observation"]["validity_time"] == "2026-10-13T11:00:00Z"


def test_null_absent_and_zero_are_distinct(database):
    x = record(sss=None, ff=0)
    del x["t"]
    payload = service(Mock(return_value=response([x]))).weather(resort())
    observation = payload["observation"]
    assert observation["snow_depth_cm"] is None
    assert observation["temperature_c"] is None
    assert observation["wind_speed"] == 0
    assert observation["field_presence"]["sss"] == "null"
    assert observation["field_presence"]["t"] == "absent"
    assert observation["raw"]["sss"] is None
    assert observation["raw"]["ff"] == 0


def test_hourly_precipitation_and_raw_units(database):
    x = record(rr1=0.2, sss=0.05)
    del x["rr_per"]
    payload = service(Mock(side_effect=[response([]), response([x])])).weather(resort())
    o = payload["observation"]
    assert o["precipitation"] == 0.2
    assert o["precipitation_period_minutes"] == 60
    assert o["wind_speed_unit"] is None
    assert o["precipitation_unit"] is None
    assert o["snow_depth_cm"] == 5
    assert o["raw"]["sss"] == 0.05
    assert o["raw"]["t"] == 283.55
    assert o["field_presence"]["rr_per"] == "absent"


@pytest.mark.parametrize("slug,sid,lat,lon,expected", [
    ("la-colmiane", "06153400", 44.07173, 7.218139, 5.587),
    ("luz-ardiden", "65413400", 42.88509, -0.05882, 4.147),
    ("val-thorens", "73257005", 45.29777, 6.58377, 0.472),
])
def test_three_validated_mappings_persist(database, slug, sid, lat, lon, expected):
    r = Resort.create(id=slug, slug=slug, name=slug, latitude=lat, longitude=lon)
    svc = service()
    mapped = svc.mapping(r)
    saved = MeteoFranceStationMapping.get(MeteoFranceStationMapping.resort == r.id)
    assert mapped["id"] == saved.meteo_france_station_id == sid
    assert saved.is_manual_override is True
    assert mapped["distance_km"] == pytest.approx(expected, abs=0.001)
    assert svc.mapping(r)["id"] == sid
    assert MeteoFranceStationMapping.select().count() == 1


def test_manual_override_wins_over_validated_default(database):
    r = resort()
    svc = service()
    svc.mapping(r)
    svc.stations = Mock(return_value=[VALIDATED_MAPPINGS["la-colmiane"]])
    svc.set_manual_mapping(r, "06153400")
    assert svc.mapping(r)["id"] == "06153400"
    with pytest.raises(MeteoFranceError):
        svc.set_manual_mapping(r, "99999999")


def test_automatic_mapping_prefers_snow_then_distance_and_altitude(database):
    r = resort("automatic")
    svc = service(cache=Redis())
    svc.stations = Mock(return_value=[
        {"id": "00000001", "name": "Near low", "latitude": 45.29777, "longitude": 6.58377, "altitude_m": 500},
        {"id": "00000002", "name": "Near no snow", "latitude": 45.298, "longitude": 6.584, "altitude_m": 2300},
        {"id": "00000003", "name": "Snow", "latitude": 45.31, "longitude": 6.59, "altitude_m": 2300},
    ])
    def fetch(url, **kwargs):
        sid = kwargs["params"]["id_station"]
        return response([record(sid, sss=None if sid == "00000002" else 0.05)])
    svc.http_get = Mock(side_effect=fetch)
    mapping = svc.mapping(r)
    assert mapping["id"] == "00000003"
    assert mapping["is_manual_override"] is False
    assert MeteoFranceStationMapping.get().meteo_france_station_id == "00000003"


def test_automatic_mapping_prefers_fresh_observation(database):
    r = resort("automatic")
    svc = service()
    svc.stations = Mock(return_value=[
        {"id": "00000001", "name": "Stale snow", "latitude": 45.29777, "longitude": 6.58377, "altitude_m": 2300},
        {"id": "00000002", "name": "Fresh snow", "latitude": 45.31, "longitude": 6.59, "altitude_m": 2300},
    ])
    svc.observation = Mock(side_effect=lambda sid: {"record": record(sid,
        validity_time="2026-10-13T00:00:00Z" if sid == "00000001" else "2026-10-13T11:00:00Z"),
        "cache_fallback": False})
    assert svc.mapping(r)["id"] == "00000002"


def test_bad_geolocation_does_not_map_to_arbitrary_station(database):
    r = resort("automatic")
    r.latitude = None
    svc = service()
    svc.stations = Mock(return_value=list(VALIDATED_MAPPINGS.values()))
    with pytest.raises(MeteoFranceError):
        svc.mapping(r)


def test_unavailable_payload_and_public_route(database, monkeypatch):
    r = resort()
    app = Flask(__name__)
    app.register_blueprint(bp_station_weather)
    svc = service(Mock(side_effect=requests.Timeout(KEY)))
    monkeypatch.setattr("app.routes.station_weather.MeteoFranceService", lambda: svc)
    result = app.test_client().get("/api/stations/val-thorens/weather")
    payload = result.get_json()
    assert result.status_code == 200
    assert result.headers["Cache-Control"] == "no-store"
    assert payload["status"] == "unavailable"
    assert payload["observation"] is None
    assert payload["source"]["station_id"] == "73257005"
    assert KEY not in result.get_data(as_text=True)
    r.is_active = False
    r.save()
    assert app.test_client().get("/api/stations/val-thorens/weather").status_code == 404
    assert app.test_client().get("/api/stations/missing/weather").get_json() == {"error": "station_not_found"}


def test_upstream_unknown_fields_never_forwarded(database):
    http = Mock(return_value=response([record(apikey=KEY, unknown_secret=KEY)]))
    payload = service(http).weather(resort())
    assert KEY not in json.dumps(payload)


def test_factory_registers_weather_and_configuration(monkeypatch):
    monkeypatch.setenv("METEO_FRANCE_API_KEY", KEY)
    monkeypatch.setenv("METEO_FRANCE_READ_TIMEOUT", "7")
    app = create_app({"SKIP_DATABASE_INIT": True, "PUBLIC_CACHE_ENABLED": False})
    assert app.config["METEO_FRANCE_API_KEY"] == KEY
    assert app.config["METEO_FRANCE_READ_TIMEOUT"] == 7
    assert app.url_map.bind("").match("/api/stations/val-thorens/weather")[0] == "station_weather.station_weather"
    for key, value in DEFAULTS.items():
        if key != "METEO_FRANCE_READ_TIMEOUT":
            assert app.config[key] == value


def test_operator_cli_validates_and_updates_mapping(database, monkeypatch):
    r = resort()
    app = Flask(__name__)
    app.config["METEO_FRANCE_API_KEY"] = KEY
    register_admin_commands(app)
    fake_db = Mock()
    fake_db.connection_context.return_value = nullcontext()
    monkeypatch.setattr("app.cli.db", fake_db)
    catalogue = csv_content()
    http = Mock(return_value=response(content=catalogue))
    monkeypatch.setattr("app.services.meteo_france.requests.get", http)
    result = app.test_cli_runner().invoke(args=["meteo-france-map", "--slug", r.slug,
                                               "--station-id", "06153400"])
    assert result.exit_code == 0
    assert MeteoFranceStationMapping.get().meteo_france_station_id == "06153400"
    assert MeteoFranceStationMapping.get().is_manual_override is True
    assert KEY not in result.output
    bad = app.test_cli_runner().invoke(args=["meteo-france-map", "--slug", r.slug,
                                            "--station-id", "bad-id"])
    assert bad.exit_code != 0
    assert http.call_count == 1


def test_weather_works_without_mapping_table_during_rollout(database):
    r = resort()
    database.drop_tables([MeteoFranceStationMapping])
    payload = service().weather(r)
    assert payload["status"] == "available"
    assert payload["source"]["station_id"] == "73257005"


def test_configurable_freshness_and_future_measurement(database):
    r = resort()
    payload = service(METEO_FRANCE_FRESHNESS_THRESHOLD_MINUTES=1).weather(r)
    assert payload["status"] == "stale"
    http = Mock(return_value=response([record(validity_time="2026-10-13T12:00:00Z")]))
    payload = service(http).weather(r)
    assert payload["observation"]["fresh"] is False
    assert payload["observation"]["age_minutes"] == -55
