import os

from flask import Flask
from dotenv import load_dotenv
from flask_cors import CORS
from app.models.base import db
from app.models.region import Region
from app.models.resort import Resort
from app.models.piste import Piste
from app.models.lift import Lift
from app.models.resort_map import ResortMap
from app.models.station_widgets import StationWidgets   
from app.models.resort_import_history import ResortImportHistory
from app.models.admin_user import AdminUser
from app.models.admin_session import AdminSession
from app.models.admin_login_attempt import AdminLoginAttempt
from app.models.ski_pass import SkiPassSeason, SkiPassPeriod, SkiPassProduct, SkiPassPrice
from app.models.anmsm_station_mapping import AnmsmStationMapping
from app.models.station_logo_candidate import StationLogoCandidate
from app.routes.public_resorts import bp_public, bp_public_stations
from app.routes.admin_resorts import bp_admin
from app.routes.stations_widgets import bp_forfaits, bp_widgets
from app.routes.admin_stations import bp_admin_st
from app.routes.public_regions import bp_regions
from app.routes.admin_regions import bp_admin_regions
from app.routes.public_departments import bp_departments
from app.routes.uploads import bp_uploads
from app.routes.admin_resort_import import bp_resort_json
from app.services.admin_auth import protect_admin_routes
from app.routes.admin_auth import bp_admin_auth
from app.routes.admin_indexnow import bp_admin_indexnow
from app.routes.admin_cache import bp_admin_cache
from app.routes.admin_station_logos import bp_admin_station_logos
from app.routes.ski_passes import (
    bp_admin_ski_passes, bp_admin_station_ski_passes, bp_public_station_ski_passes,
    bp_ski_passes,
)
from app.services.public_cache import configure_cache_logging, log_cache_startup
from app.cli import register_admin_commands



def _env_bool(name, default):
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _env_cookie_samesite():
    value = os.getenv("ADMIN_COOKIE_SAMESITE", "Lax").strip().lower()
    values = {"lax": "Lax", "strict": "Strict", "none": "None"}
    if value not in values:
        raise ValueError("ADMIN_COOKIE_SAMESITE must be Lax, Strict, or None")
    return values[value]


def _env_list(name, default=""):
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


def create_app(config=None):
    load_dotenv()
    app = Flask(__name__)
    # These are the two production origins already used by Snow Explorer.  In
    # particular, keep a safe admin default: an empty origin list makes every
    # browser preflight fail when ADMIN_ALLOWED_ORIGINS is not set on Render.
    snow_explorer_origins = "https://www.snow-explorer.com,https://snow-explorer.com"
    app.config.update(
        RESORT_IMPORT_SECRET=os.getenv("RESORT_IMPORT_SECRET"),
        ADMIN_SESSION_SECRET=os.getenv("ADMIN_SESSION_SECRET"),
        ADMIN_SESSION_COOKIE_NAME=os.getenv("ADMIN_SESSION_COOKIE_NAME", "admin_session"),
        ADMIN_SESSION_TTL_SECONDS=int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "28800")),
        ADMIN_SESSION_TOUCH_INTERVAL_SECONDS=int(os.getenv("ADMIN_SESSION_TOUCH_INTERVAL_SECONDS", "300")),
        ADMIN_COOKIE_SECURE=_env_bool("ADMIN_COOKIE_SECURE", True),
        ADMIN_COOKIE_SAMESITE=_env_cookie_samesite(),
        ADMIN_ALLOWED_ORIGINS=_env_list("ADMIN_ALLOWED_ORIGINS", snow_explorer_origins),
        API_ALLOWED_ORIGINS=_env_list("API_ALLOWED_ORIGINS", snow_explorer_origins),
        S3_ALLOWED_ORIGINS=_env_list("S3_ALLOWED_ORIGINS", snow_explorer_origins),
        ADMIN_LOGIN_RATE_LIMIT=int(os.getenv("ADMIN_LOGIN_RATE_LIMIT", "5")),
        ADMIN_LOGIN_RATE_WINDOW_SECONDS=int(os.getenv("ADMIN_LOGIN_RATE_WINDOW_SECONDS", "900")),
        TRUST_PROXY_HEADERS=_env_bool("TRUST_PROXY_HEADERS", False),
        INDEXNOW_KEY=os.getenv("INDEXNOW_KEY"),
        REDIS_URL=os.getenv("REDIS_URL"),
        PUBLIC_CACHE_ENABLED=_env_bool("PUBLIC_CACHE_ENABLED", True),
        PUBLIC_CACHE_DIRECTORY_TTL_SECONDS=int(os.getenv("PUBLIC_CACHE_DIRECTORY_TTL_SECONDS", "86400")),
        PUBLIC_CACHE_STATION_TTL_SECONDS=int(os.getenv("PUBLIC_CACHE_STATION_TTL_SECONDS", "86400")),
        PUBLIC_CACHE_WIDGETS_TTL_SECONDS=int(os.getenv("PUBLIC_CACHE_WIDGETS_TTL_SECONDS", "86400")),
        PUBLIC_CACHE_SKI_PASSES_TTL_SECONDS=int(os.getenv("PUBLIC_CACHE_SKI_PASSES_TTL_SECONDS", "21600")),
        PUBLIC_CACHE_REGIONS_TTL_SECONDS=int(os.getenv("PUBLIC_CACHE_REGIONS_TTL_SECONDS", "86400")),
        PUBLIC_CACHE_LOCK_TTL_SECONDS=int(os.getenv("PUBLIC_CACHE_LOCK_TTL_SECONDS", "10")),
        PUBLIC_CACHE_LOCK_WAIT_SECONDS=float(os.getenv("PUBLIC_CACHE_LOCK_WAIT_SECONDS", "0.2")),
        PUBLIC_CACHE_DEBUG_HEADERS=_env_bool("PUBLIC_CACHE_DEBUG_HEADERS", False),
        ANMSM_STATIONS_FEED_URL=os.getenv("ANMSM_STATIONS_FEED_URL"),
        ANMSM_LOGO_MAX_DOWNLOAD_BYTES=int(os.getenv("ANMSM_LOGO_MAX_DOWNLOAD_BYTES", str(10 * 1024 * 1024))),
    )
    if config:
        app.config.update(config)

    app.extensions["public_cache_redis"] = app.config.get("PUBLIC_CACHE_REDIS")
    if (app.extensions["public_cache_redis"] is None and app.config["PUBLIC_CACHE_ENABLED"]
            and app.config.get("REDIS_URL")):
        import redis
        # Connections remain lazy: startup and Redis outages never block the API.
        app.extensions["public_cache_redis"] = redis.Redis.from_url(
            app.config["REDIS_URL"], socket_connect_timeout=0.15,
            socket_timeout=0.2, retry_on_timeout=False,
        )
    configure_cache_logging(app.logger)
    log_cache_startup(
        app.config["PUBLIC_CACHE_ENABLED"],
        bool(app.config.get("REDIS_URL")),
        app.extensions["public_cache_redis"] is not None,
    )

    @app.before_request
    def open_database_connection():
        """Give every request its own Peewee connection lifecycle."""
        if db.is_closed():
            db.connect()

    @app.teardown_request
    def close_database_connection(_exception):
        """Always return the request connection to the pool, even on failure."""
        if not db.is_closed():
            db.close()

    # La protection est centralisée afin qu'aucune route d'administration,
    # présente ou ajoutée plus tard, ne puisse être oubliée.
    protect_admin_routes(app)

    # CORS pour le front Next.js
    CORS(app, resources={
        r"/api/s3/presign": {
            "origins": app.config["S3_ALLOWED_ORIGINS"],
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "X-CSRF-Token"],
            "methods": ["OPTIONS", "POST"],
        },
        r"/api/admin/*": {
            "origins": app.config["ADMIN_ALLOWED_ORIGINS"],
            "supports_credentials": True,
            "allow_headers": ["Content-Type", "X-CSRF-Token", "Authorization"],
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        },
        r"/api/*": {
            "origins": app.config["API_ALLOWED_ORIGINS"],
            "supports_credentials": False,
            "allow_headers": ["Content-Type", "X-CSRF-Token"],
        },
    })

    # Connexion à la base et création des tables
    if not app.config.get("SKIP_DATABASE_INIT"):
        db.connect(reuse_if_open=True)
        db.create_tables([Region, Resort, Piste, Lift, ResortMap, StationWidgets, ResortImportHistory,
                          AdminUser, AdminSession, AdminLoginAttempt, SkiPassSeason,
                          SkiPassPeriod, SkiPassProduct, SkiPassPrice,
                          AnmsmStationMapping, StationLogoCandidate])
        db.close()
        # ``close()`` normally returns the connection to the pool.  Startup may
        # happen in a Gunicorn master with --preload, so do not leave a socket
        # around that could subsequently be inherited by forked workers.
        db.close_idle()

    # Enregistrement des blueprints
    app.register_blueprint(bp_public)
    app.register_blueprint(bp_public_stations)
    app.register_blueprint(bp_admin)
    app.register_blueprint(bp_widgets)  # ⬅️ Enregistrement widgets
    app.register_blueprint(bp_forfaits)
    app.register_blueprint(bp_admin_st)
    app.register_blueprint(bp_regions)
    app.register_blueprint(bp_admin_regions)
    app.register_blueprint(bp_departments)
    app.register_blueprint(bp_uploads)
    app.register_blueprint(bp_resort_json)
    app.register_blueprint(bp_admin_auth)
    app.register_blueprint(bp_admin_indexnow)
    app.register_blueprint(bp_admin_cache)
    app.register_blueprint(bp_admin_station_logos)
    app.register_blueprint(bp_ski_passes)
    app.register_blueprint(bp_public_station_ski_passes)
    app.register_blueprint(bp_admin_ski_passes)
    app.register_blueprint(bp_admin_station_ski_passes)
    # Le front historique utilise ``/api/admin/stations`` tandis que les
    # routes d'import/export ont d'abord été publiées sous ``resorts``.
    # Enregistrer le même blueprint une seconde fois garde les deux contrats
    # disponibles (Flask exige un nom distinct pour ce second montage).
    app.register_blueprint(
        bp_resort_json,
        url_prefix="/api/admin/stations",
        name="admin_station_json",
    )

    register_admin_commands(app)
    return app
