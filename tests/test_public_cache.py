import fnmatch
import json
import logging

from flask import Flask, jsonify

from app.services.public_cache import (cached_json, invalidate_station,
                                       log_cache_startup, resorts_list_key)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ttls = {}

    def get(self, key): return self.data.get(key)
    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data: return False
        self.data[key] = value
        return True
    def setex(self, key, ttl, value):
        self.data[key], self.ttls[key] = value, ttl
    def delete(self, *keys):
        count = sum(key in self.data for key in keys)
        for key in keys: self.data.pop(key, None)
        return count
    def scan_iter(self, match, count=100):
        return iter([key for key in self.data if fnmatch.fnmatch(key, match)])


def make_app(redis):
    app = Flask(__name__)
    app.config.update(TESTING=True, PUBLIC_CACHE_REDIS=redis, PUBLIC_CACHE_DEBUG_HEADERS=True,
                      PUBLIC_CACHE_DIRECTORY_TTL_SECONDS=123,
                      PUBLIC_CACHE_LOCK_TTL_SECONDS=10,
                      PUBLIC_CACHE_LOCK_WAIT_SECONDS=0)
    app.extensions["public_cache_redis"] = redis
    calls = []

    @app.get("/api/resorts/")
    @cached_json(lambda: resorts_list_key(), "PUBLIC_CACHE_DIRECTORY_TTL_SECONDS")
    def view():
        calls.append(1)
        return jsonify({"calls": len(calls), "q": request.args.get("q")})

    return app, calls


from flask import request


def test_miss_fill_ttl_then_hit_and_query_isolation():
    redis = FakeRedis()
    app, calls = make_app(redis)
    client = app.test_client()
    first = client.get("/api/resorts/?active=true&q=Alps&limit=2")
    second = client.get("/api/resorts/?limit=2&q=Alps&active=true")
    other = client.get("/api/resorts/?active=true&q=Pyrenees&limit=2")
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert other.headers["X-Cache"] == "MISS"
    assert len(calls) == 2
    assert set(redis.ttls.values()) == {123}


def test_hit_does_not_execute_business_sql():
    redis = FakeRedis()
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        PUBLIC_CACHE_DEBUG_HEADERS=True,
        PUBLIC_CACHE_DIRECTORY_TTL_SECONDS=123,
        PUBLIC_CACHE_LOCK_TTL_SECONDS=10,
        PUBLIC_CACHE_LOCK_WAIT_SECONDS=0,
    )
    app.extensions["public_cache_redis"] = redis
    sql_calls = []

    @app.get("/api/resorts/")
    @cached_json(lambda: resorts_list_key(), "PUBLIC_CACHE_DIRECTORY_TTL_SECONDS")
    def view():
        # This callable stands in for the route's business query.  A cache hit
        # must return before Flask invokes the view at all.
        sql_calls.append("SELECT public resorts")
        return jsonify({"rows": []})

    client = app.test_client()
    first = client.get("/api/resorts/?active=true")
    second = client.get("/api/resorts/?active=true")

    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert sql_calls == ["SELECT public resorts"]


def test_cache_info_logs_are_observable(caplog):
    with caplog.at_level(logging.INFO, logger="snow.public_cache"):
        log_cache_startup(True, True, True)

    assert "PUBLIC CACHE enabled=true redis_configured=true client_initialized=true" in caplog.text


def test_corrupt_value_falls_back_and_repairs_cache():
    redis = FakeRedis()
    app, calls = make_app(redis)
    with app.test_request_context("/api/resorts/"):
        key = resorts_list_key()
    redis.data[key] = "not-json"
    response = app.test_client().get("/api/resorts/")
    assert response.status_code == 200
    assert len(calls) == 1
    assert json.loads(redis.data[key])["status"] == 200


def test_redis_failure_bypasses_cache():
    class Broken(FakeRedis):
        def get(self, key): raise ConnectionError("down")
    app, calls = make_app(Broken())
    assert app.test_client().get("/api/resorts/").status_code == 200
    assert len(calls) == 1


def test_station_invalidation_removes_all_dependent_keys():
    redis = FakeRedis()
    app, _ = make_app(redis)
    redis.data.update({
        "snow:public:station:chamonix": "x", "snow:public:widgets:chamonix": "x",
        "snow:public:skipasses:chamonix": "x", "snow:public:resorts:list:abc": "x",
        "snow:public:station:tignes": "keep",
    })
    with app.app_context(): invalidate_station("Chamonix")
    assert redis.data == {"snow:public:station:tignes": "keep"}
