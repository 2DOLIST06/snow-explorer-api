"""Explicit, insert-only preparation of editorial pages from stored station IDs."""
import re

from peewee import PostgresqlDatabase

from app.datetime_utils import utcnow
from app.services.region_ids import canonical_region_id


class RegionPagePreparationError(ValueError):
    pass


REQUIRED_COLUMNS = {
    "regions": {"id", "name", "slug", "country_code", "seo_text", "meta_title",
                "meta_description", "created_at", "updated_at"},
    "resort": {"region_id", "region_name", "country_code", "is_active"},
}
EDITORIAL_REGION_IDS = frozenset({
    "auvergne-rhone-alpes", "bourgogne-franche-comte", "grand-est",
    "nouvelle-aquitaine", "occitanie", "provence-alpes-cote-d-azur",
})


def _plan(database):
    for table, required in REQUIRED_COLUMNS.items():
        actual = {column.name for column in database.get_columns(table)}
        missing = required - actual
        if missing:
            raise RegionPagePreparationError(
                f"{table}: missing required columns: {', '.join(sorted(missing))}")

    stored = dict(database.execute_sql('SELECT "id", "slug" FROM "regions"').fetchall())
    slug_owners = {slug: region_id for region_id, slug in stored.items()}
    rows = database.execute_sql('''
        SELECT "region_id", "region_name", "country_code", COUNT(*),
               SUM(CASE WHEN "is_active" THEN 1 ELSE 0 END)
        FROM "resort"
        WHERE "region_id" IS NOT NULL AND TRIM("region_id") <> ''
        GROUP BY "region_id", "region_name", "country_code"
        ORDER BY "region_id", "region_name", "country_code"
    ''').fetchall()
    candidates = {}
    public_ids = {}
    excluded = []
    for region_id, name, country, station_count, active_count in rows:
        if not isinstance(region_id, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", region_id) or len(region_id) > 255:
            raise RegionPagePreparationError(f"Invalid stored region ID: {region_id!r}")
        if not isinstance(name, str) or not name.strip() or len(name) > 255:
            raise RegionPagePreparationError(f"Missing or invalid name for {region_id}")
        if not isinstance(country, str) or not re.fullmatch(r"[A-Z]{2}", country):
            raise RegionPagePreparationError(f"Missing or invalid country for {region_id}")
        if region_id in candidates:
            raise RegionPagePreparationError(f"Conflicting station names/countries for {region_id}")
        public_id = canonical_region_id(region_id)
        if public_id not in EDITORIAL_REGION_IDS:
            excluded.append({"id": region_id, "name": name, "country_code": country,
                             "stations": station_count, "active_stations": active_count,
                             "reason": "not_an_editorial_region"})
            continue
        if public_id in public_ids:
            raise RegionPagePreparationError(f"Multiple station IDs resolve to {public_id}; review required")
        public_ids[public_id] = region_id
        candidates[region_id] = {
            "id": region_id, "slug": region_id, "name": name, "country_code": country,
            "public_id": public_id, "stations": station_count, "active_stations": active_count,
        }

    missing = []
    existing = []
    for region_id, row in candidates.items():
        if region_id in stored:
            existing.append(region_id)
            continue
        # Do not create a second editorial record for an existing API alias.
        aliases = [stored_id for stored_id in stored
                   if canonical_region_id(stored_id) == row["public_id"]]
        if aliases:
            existing.extend(aliases)
            continue
        if row["slug"] in slug_owners:
            raise RegionPagePreparationError(f"Slug {row['slug']} already belongs to another region")
        missing.append(row)
    return {"missing": missing, "existing_ids": sorted(set(existing)), "excluded": excluded}


def prepare_region_pages(database, *, apply=False):
    """Preview by default. Explicit apply inserts missing rows in one transaction.

    No model/schema initialization, UPDATE, DELETE, or station write occurs here.
    The caller supplies an already configured database and owns its connection.
    """
    transaction = (database.atomic(isolation_level="REPEATABLE READ")
                   if isinstance(database, PostgresqlDatabase) else database.atomic())
    with transaction:
        if isinstance(database, PostgresqlDatabase) and not apply:
            database.execute_sql("SET TRANSACTION READ ONLY")
        plan = _plan(database)
        inserted = []
        if apply:
            # Production timestamps are naive UTC, without database defaults.
            now = utcnow().replace(tzinfo=None)
            placeholders = ", ".join([database.param] * 6)
            sql = f'''INSERT INTO "regions"
                ("id", "name", "slug", "country_code", "created_at", "updated_at",
                 "seo_text", "meta_title", "meta_description")
                VALUES ({placeholders}, NULL, NULL, NULL)
                ON CONFLICT ("id") DO NOTHING RETURNING "id"'''
            for row in plan["missing"]:
                result = database.execute_sql(sql, (
                    row["id"], row["name"], row["slug"], row["country_code"], now, now,
                )).fetchone()
                if result:
                    inserted.append(result[0])
    return {"mode": "apply" if apply else "preview", **plan, "inserted_ids": inserted}
