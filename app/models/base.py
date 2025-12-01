from peewee import PostgresqlDatabase, Model
import os
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

db = PostgresqlDatabase(
    cfg["database"],
    user=cfg["user"],
    password=cfg["password"],
    host=cfg["host"],
    port=cfg["port"],
)

class BaseModel(Model):
    class Meta:
        database = db



