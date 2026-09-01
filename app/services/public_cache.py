"""Fault-tolerant Redis cache-aside support for public HTTP responses."""
import hashlib
import json
import logging
import re
import time
from functools import wraps

from flask import current_app, make_response, request

logger = logging.getLogger("snow.public_cache")
# Gunicorn configures its own loggers, while the application root logger keeps
# the default WARNING threshold.  Give cache lifecycle records an explicit
# level so INFO events propagate to Render's process log instead of being
# discarded before they reach a handler.
logger.setLevel(logging.INFO)
_SAFE_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def log_cache_startup(enabled, redis_configured, client_initialized):
    """Report cache wiring without exposing the Redis URL or credentials."""
    logger.info(
        "PUBLIC CACHE enabled=%s redis_configured=%s client_initialized=%s",
        str(bool(enabled)).lower(),
        str(bool(redis_configured)).lower(),
        str(bool(client_initialized)).lower(),
    )


def configure_cache_logging(application_logger):
    """Use Flask's stderr handler, which Gunicorn/Render actually captures."""
    if not logger.handlers:
        for handler in application_logger.handlers:
            logger.addHandler(handler)


def normalize_component(value):
    value = (value or "").strip().lower()
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError("unsafe cache key component")
    return value


def query_variation(names):
    """Stable, bounded key suffix containing only response-changing arguments."""
    values = []
    for name in sorted(names):
        values.append((name, request.args.getlist(name)))
    canonical = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:24]


def resorts_list_key():
    return f"snow:public:resorts:list:{query_variation(('active', 'limit', 'q'))}"


def station_key(slug):
    return f"snow:public:station:{normalize_component(slug)}"


def widgets_key(slug):
    return f"snow:public:widgets:{normalize_component(slug)}"


def ski_passes_key(slug):
    suffix = query_variation(("season",)) if request.args.get("season") is not None else None
    base = f"snow:public:skipasses:{normalize_component(slug)}"
    return f"{base}:{suffix}" if suffix else base


def region_key(slug):
    return f"snow:public:region:{normalize_component(slug)}"


def _client():
    return current_app.extensions.get("public_cache_redis")


def _debug_header(response, state):
    if (current_app.config.get("PUBLIC_CACHE_DEBUG_HEADERS")
            and (current_app.debug or current_app.testing)):
        response.headers["X-Cache"] = state
    return response


def _deserialize(raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("body"), str):
        raise ValueError("invalid cache envelope")
    return value


def cached_json(key_factory, ttl_config):
    """Cache successful JSON Flask responses; all Redis failures fail open."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            client = _client()
            if client is None:
                logger.info("CACHE BYPASS path=%s", request.path)
                return _debug_header(make_response(view(*args, **kwargs)), "BYPASS")
            try:
                key = key_factory(*args, **kwargs)
            except ValueError:
                logger.info("CACHE BYPASS path=%s reason=invalid-key", request.path)
                return _debug_header(make_response(view(*args, **kwargs)), "BYPASS")
            try:
                raw = client.get(key)
                if raw is not None:
                    try:
                        cached = _deserialize(raw)
                    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                        logger.warning("CACHE ERROR key=%s reason=corrupt", key)
                        try:
                            client.delete(key)
                        except Exception:
                            logger.exception("CACHE ERROR key=%s operation=delete-corrupt", key)
                    else:
                        response = make_response(cached["body"], cached.get("status", 200))
                        response.headers["Content-Type"] = cached.get("content_type", "application/json")
                        for name, value in cached.get("headers", {}).items():
                            response.headers[name] = value
                        logger.info("CACHE HIT key=%s", key)
                        return _debug_header(response, "HIT")
                logger.info("CACHE MISS key=%s", key)
            except Exception:
                logger.exception("CACHE ERROR key=%s operation=get", key)
                return _debug_header(make_response(view(*args, **kwargs)), "BYPASS")

            # A short distributed lock limits duplicate rebuilds across workers.
            lock_key = f"{key}:lock"
            owns_lock = False
            try:
                owns_lock = bool(client.set(lock_key, "1", nx=True, ex=current_app.config["PUBLIC_CACHE_LOCK_TTL_SECONDS"]))
                if not owns_lock:
                    deadline = time.monotonic() + current_app.config["PUBLIC_CACHE_LOCK_WAIT_SECONDS"]
                    while time.monotonic() < deadline:
                        time.sleep(0.02)
                        raw = client.get(key)
                        if raw is not None:
                            cached = _deserialize(raw)
                            response = make_response(cached["body"], cached.get("status", 200))
                            response.headers["Content-Type"] = cached.get("content_type", "application/json")
                            logger.info("CACHE HIT key=%s after-wait=true", key)
                            return _debug_header(response, "HIT")
            except Exception:
                logger.exception("CACHE ERROR key=%s operation=lock", key)

            response = make_response(view(*args, **kwargs))
            if 200 <= response.status_code < 300 and response.is_json:
                envelope = json.dumps({
                    "body": response.get_data(as_text=True), "status": response.status_code,
                    "content_type": response.content_type,
                    "headers": {name: response.headers[name] for name in ("Cache-Control", "X-Public-Resorts-Version") if name in response.headers},
                }, separators=(",", ":"))
                try:
                    client.setex(key, int(current_app.config[ttl_config]), envelope)
                except Exception:
                    logger.exception("CACHE ERROR key=%s operation=set", key)
            if owns_lock:
                try:
                    client.delete(lock_key)
                except Exception:
                    logger.exception("CACHE ERROR key=%s operation=unlock", key)
            return _debug_header(response, "MISS")
        return wrapped
    return decorate


def invalidate_patterns(*patterns):
    client = _client()
    if client is None:
        logger.info("CACHE BYPASS operation=invalidate")
        return 0
    deleted = 0
    try:
        for pattern in patterns:
            keys = list(client.scan_iter(match=pattern, count=100))
            if keys:
                deleted += client.delete(*keys)
        logger.info("CACHE INVALIDATE patterns=%s deleted=%d", len(patterns), deleted)
    except Exception:
        logger.exception("CACHE ERROR operation=invalidate")
    return deleted


def invalidate_station(slug, include_directory=True):
    try:
        safe = normalize_component(slug)
    except ValueError:
        logger.warning("CACHE ERROR operation=invalidate reason=invalid-slug")
        return 0
    patterns = [f"snow:public:station:{safe}", f"snow:public:widgets:{safe}", f"snow:public:skipasses:{safe}*"]
    if include_directory:
        patterns.append("snow:public:resorts:list:*")
    return invalidate_patterns(*patterns)


def invalidate_ski_passes(slug):
    try:
        safe = normalize_component(slug)
    except ValueError:
        logger.warning("CACHE ERROR operation=invalidate-ski-passes reason=invalid-slug")
        return 0
    return invalidate_patterns(f"snow:public:skipasses:{safe}*", f"snow:public:station:{safe}")


def invalidate_widgets(slug):
    try:
        safe = normalize_component(slug)
    except ValueError:
        logger.warning("CACHE ERROR operation=invalidate-widgets reason=invalid-slug")
        return 0
    return invalidate_patterns(f"snow:public:widgets:{safe}", f"snow:public:station:{safe}", "snow:public:resorts:list:*")


def purge_directory():
    return invalidate_patterns("snow:public:resorts:list:*")


def purge_all():
    return invalidate_patterns("snow:public:*")


def invalidate_region(slug):
    try:
        safe = normalize_component(slug)
    except ValueError:
        logger.warning("CACHE ERROR operation=invalidate-region reason=invalid-slug")
        return 0
    return invalidate_patterns("snow:public:regions:list", f"snow:public:region:{safe}",
                               "snow:public:resorts:list:*", "snow:public:station:*")


# Kept for compatibility with the existing response header.
_public_resorts_version = 1
def get_public_resorts_version(): return _public_resorts_version
def bump_public_resorts_version():
    global _public_resorts_version
    _public_resorts_version += 1
    return _public_resorts_version
