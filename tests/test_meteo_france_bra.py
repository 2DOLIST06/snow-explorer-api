import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from flask import Flask
from peewee import SqliteDatabase

from app import create_app
from app.models.meteo_france_massif_override import MeteoFranceMassifOverride
from app.models.region import Region
from app.models.resort import Resort
from app.routes.station_avalanche_risk import bp_station_avalanche_risk
from app.services.meteo_france_bra import (
    BASE_URL, BraError, BraService, TTLCache, bulletin_reason, map_massif, parse_massifs,
)

KEY = "unit-test-bra-secret"
SEASON_XML = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n'
              + '<message><![CDATA[Bulletin avalanche : la saison est terminée sur le massif Mercantour, rendez-vous début novembre.]]></message>'.encode())
PNG = b"\x89PNG\r\n\x1a\n" + b"image-fixture"
PDF = b"%PDF-1.7\nfixture"


def feature(code=23, title="Mercantour", xmin=7, xmax=8, holes=None):
    return {"type": "Feature", "properties": {
        "code": code, "title": title, "mountain": "Alpes du Sud", "title_short": "Merca",
        "lon_center": 7.5, "lat_center": 44.5, "Departement": "Alpes Maritimes", "Dep2": None,
    }, "geometry": {"type": "MultiPolygon", "coordinates": [[
        [[xmin, 44], [xmax, 44], [xmax, 45], [xmin, 45], [xmin, 44]], *(holes or [])
    ]]}}


def catalogue(*features):
    return {"type": "FeatureCollection", "name": "Metadata_massif_DP_2024",
            "features": list(features or (feature(),))}


def station():
    return SimpleNamespace(id="isola", slug="isola-2000", name="Isola 2000", latitude=44.5, longitude=7.5)


def response(content, mime, status=200):
    result = Mock(status_code=status, headers={"Content-Type": mime})
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.iter_content.return_value = [content]
    return result


def http_stub(xml=SEASON_XML, xml_status=200, asset_status=200):
    def get(url, params=None, **kwargs):
        if url.endswith("/liste-massifs"):
            return response(json.dumps(catalogue()).encode(), "application/geo+json")
        if params.get("format") == "xml":
            return response(xml, "text/xml; charset=utf-8", xml_status)
        if params.get("format") == "pdf":
            return response(PDF, "application/pdf", asset_status)
        return response(PNG, "image/png", asset_status)
    return Mock(side_effect=get)


def service(http=None, **kwargs):
    return BraService(config={"METEO_FRANCE_BRA_API_KEY": KEY},
                      http_get=http or http_stub(), override_lookup=lambda r: None, **kwargs)


def test_official_geojson_and_identity():
    s = service()
    data = s.massifs()
    assert data == catalogue()
    assert set(data["features"][0]["properties"]) == {
        "code", "mountain", "title", "title_short", "lon_center", "lat_center", "Departement", "Dep2"}
    assert map_massif(data, 44.5, 7.5) == {
        "mapping_status": "confirmed", "id": 23, "name": "Mercantour", "mapping_method": "automatic"}
    call = s.http_get.call_args
    assert call.args == (BASE_URL + "/liste-massifs",)
    assert call.kwargs["headers"] == {"apikey": KEY}
    assert call.kwargs["allow_redirects"] is False
    assert call.kwargs["timeout"] == (3.05, 5)


@pytest.mark.parametrize("lat,lon,status", [
    (44.5, 7.5, "confirmed"), (7.5, 44.5, "unmapped"), (0, 0, "unmapped"),
    (44, 7.5, "ambiguous"), (44, 7, "ambiguous"),
    (None, 7.5, "unmapped"), (float("nan"), 7.5, "unmapped"), (91, 7.5, "unmapped"),
])
def test_point_in_polygon(lat, lon, status):
    assert map_massif(catalogue(), lat, lon)["mapping_status"] == status


def test_overlap_and_shared_boundary():
    data = catalogue(feature(), feature(22, "Haut-Var Haut-Verdon", 7.4, 8.4))
    assert map_massif(data, 44.5, 7.5)["mapping_status"] == "ambiguous"
    data = catalogue(feature(), feature(22, "Haut-Var Haut-Verdon", 8, 9))
    assert map_massif(data, 44.5, 8)["mapping_status"] == "ambiguous"


def test_holes_and_multiple_polygons():
    hole = [[7.2, 44.2], [7.8, 44.2], [7.8, 44.8], [7.2, 44.8], [7.2, 44.2]]
    f = feature(holes=[hole])
    assert map_massif(catalogue(f), 44.5, 7.5)["mapping_status"] == "unmapped"
    assert map_massif(catalogue(f), 44.2, 7.5)["mapping_status"] == "ambiguous"
    f["geometry"]["coordinates"].extend(feature(xmin=9, xmax=10)["geometry"]["coordinates"])
    assert map_massif(catalogue(f), 44.5, 9.5)["id"] == 23


def test_manual_override_priority_and_invalid_override():
    data = catalogue(feature(), feature(22, "Haut-Var Haut-Verdon", 9, 10))
    assert map_massif(data, 44.5, 7.5, 22) == {
        "mapping_status": "confirmed", "id": 22, "name": "Haut-Var Haut-Verdon", "mapping_method": "manual"}
    assert map_massif(data, None, None, 23)["id"] == 23
    assert map_massif(data, 44.5, 7.5, 999)["mapping_status"] == "unmapped"


@pytest.mark.parametrize("change", [
    lambda d: d["features"][0]["properties"].update(code=True),
    lambda d: d["features"][0]["properties"].pop("title"),
    lambda d: d["features"][0]["geometry"].update(type="Point"),
    lambda d: d["features"][0]["geometry"]["coordinates"][0][0].pop(),
    lambda d: d["features"].append(d["features"][0]),
])
def test_bad_geojson_fails_closed(change):
    data = catalogue()
    change(data)
    with pytest.raises(BraError, match="invalid_massif_catalogue"):
        parse_massifs(json.dumps(data).encode())


def test_http_200_season_message_is_unavailable_even_with_assets():
    result = service().risk(station())
    assert result["status"] == "unavailable"
    assert result["unavailable_reason"] == "season_ended"
    assert all(value is None for value in result["risk"].values())
    assert result["massif"]["name"] == "Mercantour"
    assert result["source"]["scope"] == "massif"
    assert result["assets"] == {"bulletin_pdf_available": True, "risk_image_available": True, "current": False}
    assert KEY not in json.dumps(result)
    assert "public-api.meteofrance.fr" not in json.dumps(result)
    assert "pistes sécurisées" in result["display"]["disclaimer"]


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_upstream_statuses_remain_unavailable(status, caplog):
    result = service(http_stub(xml_status=status)).risk(station())
    assert result["status"] == "unavailable"
    assert result["unavailable_reason"] == "upstream_unavailable"
    assert result["risk"]["level"] is None
    assert KEY not in caplog.text


@pytest.mark.parametrize("xml", [
    b'<BRA risk="5"><massif>invented</massif></BRA>', b"broken",
    b'<message><risk>1</risk>la saison est termin\xc3\xa9e</message>',
    b'<!DOCTYPE message [<!ENTITY x "secret">]><message>&x;</message>',
    '<!DOCTYPE message><message/>'.encode("utf-16"),
])
def test_unknown_schema_not_parsed_and_logs_only_structure(xml, caplog):
    result = service(http_stub(xml=xml)).risk(station())
    assert result["unavailable_reason"] == "unsupported_bulletin_schema"
    assert all(v is None for v in result["risk"].values())
    assert "invented" not in caplog.text
    assert KEY not in caplog.text


def test_unsupported_schema_logs_no_text_or_attribute_values(caplog):
    assert bulletin_reason(b'<Active secret="private-value"><Text>private-text</Text></Active>') == "unsupported_bulletin_schema"
    assert "elements=2" in caplog.text
    assert "private-value" not in caplog.text and "private-text" not in caplog.text


def test_transport_timeout_and_missing_key(caplog):
    http = Mock(side_effect=requests.Timeout(KEY))
    assert service(http).risk(station())["unavailable_reason"] == "upstream_unavailable"
    assert KEY not in caplog.text
    s = BraService(config={}, http_get=http)
    with pytest.raises(BraError, match="not_configured"):
        s.massifs()


def test_response_size_limit_and_closed_connection():
    r = response(b"x" * 20, "text/xml")
    with pytest.raises(BraError, match="response_limit"):
        service(Mock(return_value=r))._get("/massif/BRA", {}, {"text/xml"}, 10)
    r.__exit__.assert_called_once()


def test_error_body_never_read_or_forwarded(caplog):
    r = response(KEY.encode() + b"private upstream details", "text/xml", 403)
    with pytest.raises(BraError, match="forbidden"):
        service(Mock(return_value=r))._get("/massif/BRA", {}, {"text/xml"}, 1000)
    r.iter_content.assert_not_called()
    assert KEY not in caplog.text and "private upstream details" not in caplog.text


def test_cache_entry_and_asset_memory_bounds():
    cache = TTLCache(max_entries=2, max_asset_bytes=5)
    cache.put("one", (b"1234", "image/png"), 300)
    cache.put("two", (b"5678", "image/png"), 300)
    assert cache.get("one") is None
    assert cache.get("two") is not None
    cache.put("three", "x", 300)
    cache.put("four", "y", 300)
    assert cache.get("two") is None


@pytest.mark.parametrize("mime,body,code", [
    ("text/html", KEY.encode(), 200), ("image/png", b"<html/>", 200),
    ("image/png", PNG + KEY.encode(), 200), ("image/png", PNG, 302),
])
def test_asset_validation_and_no_credential_leak(mime, body, code, caplog):
    http = Mock(return_value=response(body, mime, code))
    with pytest.raises(BraError) as caught:
        service(http).asset(23, "risk")
    assert KEY not in str(caught.value) and KEY not in caplog.text


def test_asset_404_and_errors_availability():
    assert service(http_stub(asset_status=404)).risk(station())["assets"]["bulletin_pdf_available"] is False
    assert service(http_stub(asset_status=403)).risk(station())["assets"]["risk_image_available"] is None


def test_cache_ttls_expiry_and_no_last_good():
    clock = [0]
    cache = TTLCache(clock=lambda: clock[0])
    http = http_stub()
    s = service(http, cache=cache)
    s.risk(station())
    assert http.call_count == 4
    s.risk(station())
    assert http.call_count == 4
    clock[0] = 301
    s.risk(station())
    assert http.call_count == 7  # catalogue/mapping remain cached, XML/assets refreshed
    clock[0] = 86401
    http.side_effect = requests.Timeout(KEY)
    result = s.risk(station())
    assert result["risk"]["level"] is None and result["massif"] is None


def test_mapping_cache_invalidation_coordinates_override_catalogue():
    s = service()
    r = station()
    assert s.mapping(r)["id"] == 23
    r.longitude = 0
    assert s.mapping(r)["mapping_status"] == "unmapped"
    s.override_lookup = lambda r: 23
    assert s.mapping(r)["mapping_method"] == "manual"
    data = catalogue(feature(title="Exact official name"))
    s.cache.put("massifs", data, 86400)
    assert s.mapping(r)["name"] == "Exact official name"


@pytest.fixture
def client(monkeypatch):
    database = SqliteDatabase(":memory:", pragmas={"foreign_keys": 1})
    models = [Region, Resort, MeteoFranceMassifOverride]
    with database.bind_ctx(models, bind_refs=False, bind_backrefs=False):
        database.connect()
        database.create_tables(models)
        Resort.create(id="isola", slug="isola-2000", name="Isola 2000", latitude=44.5, longitude=7.5)
        app = Flask(__name__)
        app.config.update(TESTING=True, METEO_FRANCE_BRA_API_KEY=KEY)
        app.register_blueprint(bp_station_avalanche_risk)
        monkeypatch.setattr("app.services.meteo_france_bra.requests.get", http_stub())
        yield app.test_client()
        database.close()


def test_public_endpoint_real_mapping_and_manual_persistence(client):
    response = client.get("/api/stations/isola-2000/avalanche-risk")
    assert response.status_code == 200
    assert response.json["massif"]["mapping_method"] == "automatic"
    assert response.headers["Cache-Control"] == "no-store"
    MeteoFranceMassifOverride.create(resort="isola", massif_id=23)
    assert client.get("/api/stations/isola-2000/avalanche-risk").json["massif"]["mapping_method"] == "manual"
    assert client.get("/api/stations/missing/avalanche-risk").status_code == 404
    Resort.update(is_active=False).where(Resort.id == "isola").execute()
    assert client.get("/api/stations/isola-2000/avalanche-risk").status_code == 404


@pytest.mark.parametrize("path,mime,body", [
    ("pdf", "application/pdf", PDF), ("image/risk", "image/png", PNG),
    ("image/slopes", "image/png", PNG), ("image/snow-cover", "image/png", PNG),
    ("image/fresh-snow", "image/png", PNG), ("image/weather", "image/png", PNG),
])
def test_proxy_routes_only_expected_formats(client, path, mime, body):
    r = client.get("/api/stations/isola-2000/avalanche-risk/" + path)
    assert r.status_code == 200 and r.mimetype == mime and r.data == body
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert KEY.encode() not in r.data and "Location" not in r.headers
    assert client.get("/api/stations/isola-2000/avalanche-risk").json["status"] == "unavailable"


def test_proxy_errors_and_no_arbitrary_upstream_path(client, monkeypatch):
    assert client.get("/api/stations/isola-2000/avalanche-risk/image/unknown").status_code == 404
    http = Mock(return_value=response(KEY.encode(), "text/html"))
    monkeypatch.setattr("app.services.meteo_france_bra.requests.get", http)
    r = client.get("/api/stations/isola-2000/avalanche-risk/pdf")
    assert r.status_code == 503 and KEY.encode() not in r.data


def test_no_massif_or_ambiguous_never_fetches_assets(client, monkeypatch):
    http = http_stub()
    monkeypatch.setattr("app.services.meteo_france_bra.requests.get", http)
    Resort.update(longitude=0).where(Resort.id == "isola").execute()
    r = client.get("/api/stations/isola-2000/avalanche-risk")
    assert r.json["unavailable_reason"] == "no_massif" and http.call_count == 1
    Resort.update(longitude=7).where(Resort.id == "isola").execute()
    assert client.get("/api/stations/isola-2000/avalanche-risk").json["unavailable_reason"] == "ambiguous_massif"
    assert client.get("/api/stations/isola-2000/avalanche-risk/pdf").status_code == 404


def test_blueprint_registered_in_production_app():
    app = create_app({"SKIP_DATABASE_INIT": True, "PUBLIC_CACHE_ENABLED": False})
    assert "/api/stations/<slug>/avalanche-risk" in {r.rule for r in app.url_map.iter_rules()}
