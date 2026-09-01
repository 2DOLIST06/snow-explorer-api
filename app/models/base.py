import os

from peewee import Model
from playhouse.pool import PooledPostgresqlDatabase
from dotenv import load_dotenv

load_dotenv()  # charge .env

DATABASE_URL = os.getenv("DATABASE_URL") or "postgresql://postgres:postgres@localhost:5433/ski"

def _parse_pg_url(pg_url: str):
    assert pg_url.startswith("postgresql://")
    body = pg_url[len("postgresql://"):]
    creds, hostpart = body.split("@", 1)
    user, password = creds.split(":", 1)
    hostport, dbname = hostpart.split("/", 1)
    if ":" in hostport:
        host, port = hostport.split(":", 1)
    else:
        host, port = hostport, "5432"
    return dict(user=user, password=password, host=host, port=int(port), database=dbname)

cfg = _parse_pg_url(DATABASE_URL)

# Gunicorn currently runs two synchronous workers.  Three connections per
# process leave one connection of headroom for a future thread/background task
# while keeping the production ceiling deliberately small (2 * 3 = 6).
POOL_MAX_CONNECTIONS = int(os.getenv("DB_POOL_MAX_CONNECTIONS", "3"))
POOL_STALE_TIMEOUT = int(os.getenv("DB_POOL_STALE_TIMEOUT", "300"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "5"))

db = PooledPostgresqlDatabase(
    cfg["database"],
    user=cfg["user"],
    password=cfg["password"],
    host=cfg["host"],
    port=cfg["port"],
    max_connections=POOL_MAX_CONNECTIONS,
    stale_timeout=POOL_STALE_TIMEOUT,
    timeout=POOL_TIMEOUT,
)

class BaseModel(Model):
    class Meta:
        database = db


