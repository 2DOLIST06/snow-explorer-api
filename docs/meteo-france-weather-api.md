# Météo-France DPObs v2 backend

Backend only. Open-Meteo and the existing frontend components are unchanged.
No historical observations are written to PostgreSQL. Redis stores current
observations and a bounded last-good copy for outages.

## Configuration on Render

| Variable | Default | Meaning |
| --- | --- | --- |
| `METEO_FRANCE_API_KEY` | none, required | Official Météo-France API Key, backend secret |
| `METEO_FRANCE_CONNECT_TIMEOUT` | `3.05` | Connection timeout, seconds |
| `METEO_FRANCE_READ_TIMEOUT` | `5` | Read timeout, seconds |
| `METEO_FRANCE_STATIONS_CACHE_TTL_SECONDS` | `86400` | Catalogue cache lifetime, seconds |
| `METEO_FRANCE_OBSERVATION_CACHE_TTL_SECONDS` | `300` | Observation cache lifetime, seconds |
| `METEO_FRANCE_STALE_CACHE_TTL_SECONDS` | `604800` | Last-good retention, seconds |
| `METEO_FRANCE_FRESHNESS_THRESHOLD_MINUTES` | `120` | Maximum measurement age for `fresh: true` |
| `METEO_FRANCE_MAX_CANDIDATES` | `5` | Maximum automatic candidate probes per selection |
| `METEO_FRANCE_MAX_DISTANCE_KM` | `75` | Maximum automatic matching radius |
| `REDIS_URL` | existing backend setting | Redis URL, backend secret |

Set `METEO_FRANCE_API_KEY` in Render's secret environment configuration using
the already validated key. The integration does not read the legacy
`METEO_FRANCE_ACCESS_TOKEN` variable, perform OAuth, or send a Bearer token.
Never place the key in a frontend variable, command argument, file or Git.
Timeouts, TTLs, freshness threshold, radius and candidate count must be positive.

Redis reuses the public-cache connection when available. Otherwise the weather
service lazily creates its own connection from `REDIS_URL`. Public HTTP caching
can be disabled independently. Redis failures are swallowed without logging
connection exceptions or credential-bearing URLs.

## Migration

Run before enabling the route in production:

```sh
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20261013_add_meteo_france_station_mappings.sql
```

This creates `meteo_france_station_mappings`, with one mapping per Resort,
an eight-character provider station ID, station metadata, geographic distance,
manual override flag and UTC timestamps. The Resort foreign key cascades on
deletion. Only existing Resorts with the three validated slugs are seeded.
Re-running the migration preserves existing operator mappings.

| Resort slug | Provider ID | Name | Altitude |
| --- | --- | --- | --- |
| `la-colmiane` | `06153400` | MILLEFONTS-NIVOSE | 2430 m |
| `luz-ardiden` | `65413400` | LAC D'ARDIDEN-NIVOSE | 2445 m |
| `val-thorens` | `73257005` | VAL THORENS_SAPC | 2277 m |

The existing application startup table-creation convention also includes the
new model. The SQL migration adds database constraints and seeds the mappings.
The service can serve these validated mappings during a rolling deployment
even if the mapping table has not yet been created. Persistence requires the
migration; automatic persistence errors do not block weather retrieval.

## Manual overrides and automatic matching

An existing manual database mapping wins over every automatic/default choice.
The three validated slugs use explicit defaults, persisted as manual mappings
on first access if they were not seeded. Distance is recalculated from Resort
coordinates with Haversine, using an Earth radius of 6371 km.

To validate and persist an operator override from the backend console:

```sh
flask --app app.main meteo-france-map --slug val-thorens --station-id 73257005
```

The command checks the provider catalogue, updates all metadata and preserves
the original creation timestamp. It requires backend console access, not a
public HTTP write route. It does not take credentials as arguments.

For other Resorts, automatic selection works as follows:

1. Reuse a saved automatic mapping while its observation is fresh, not an
   outage fallback, and has numeric `sss`.
2. Build candidates within the configured radius. Missing or invalid Resort
   coordinates prevent automatic selection.
3. Calculate altitude gap outside `[altitude_min_m, altitude_max_m]`, using
   `altitude_base_m` and `altitude_top_m` for missing bounds. A bound missing
   in both fields imposes no penalty on that side.
4. Shortlist by `distance_km + altitude_gap_m / 100`, then provider ID, up to
   the configured candidate limit. This bounds network work on a cold mapping.
5. Probe real observations. Exclude candidates with no usable observation.
6. Rank by fresh/non-fallback first, numeric snow second, distance/altitude
   score third, measurement age fourth, then provider ID as tie-breaker.
7. Persist the selected mapping. A concurrent manual override is never
   overwritten by automatic persistence.

This is a deterministic bounded selection, not an exhaustive observation scan
of all 2151 catalogue stations. First automatic selection may make up to ten
observation requests at the default candidate limit. The service memoizes
observations during one request to avoid probing the selected post twice.

## Provider calls and cache

The fixed provider base is:

```text
https://public-api.meteofrance.fr/public/DPObs/v2
```

Only `accept: */*` and the secret `apikey` are supplied by the integration.
Requests uses TLS verification and inherited proxy settings. Redirects are
disabled. No upstream error body, request object or arbitrary exception text
is forwarded to a public response or logged.

The catalogue uses `/liste-stations`: ISO-8859-1 CSV separated by semicolons.
IDs remain strings. Invalid coordinate/ID rows are ignored; malformed/empty
catalogues are rejected.

Observations use `id_station` and `format=json`:

1. Try `/station/infrahoraire-6m`.
2. If empty, invalid, in error or stale, try `/station/horaire`.
3. If both return usable observations, retain the newer `validity_time`.
   Equal timestamps prefer the 6-minute endpoint.
4. If no usable observation is returned, use the last-good Redis entry and
   force `fresh: false`. If none exists, return `unavailable`.

A usable record must match the requested provider ID, have a valid timezone-
aware `validity_time` and at least one finite numeric weather parameter.
All-null records and records with invalid measurement times are unusable.
Missing snow alone does not discard otherwise usable temperature/wind data.
The newest usable record in an upstream array is selected.

Keys:

| Key | TTL setting |
| --- | --- |
| `snow:meteo-france:stations` | stations TTL |
| `snow:meteo-france:stations:last-good` | stale retention TTL |
| `snow:meteo-france:observation:<station_id>` | observation TTL |
| `snow:meteo-france:observation:<station_id>:last-good` | stale retention TTL |

Hot and last-good entries are separate: expiry of the short TTL does not erase
the outage fallback. Malformed cache data is ignored. Redis read/write failures
do not prevent provider calls. Last-good data is not retained indefinitely.
The weather route is not wrapped in the public response cache and returns
`Cache-Control: no-store`, so age is recalculated for every response.

## Exact public contract

`GET /api/stations/<slug>/weather` requires no authentication. Unknown or
inactive Resorts return HTTP 404:

```json
{"error": "station_not_found"}
```

Existing active Resorts return HTTP 200 for available, stale and unavailable
weather. These provider states do not produce a weather-related HTTP 500.
Database failures during Resort lookup remain infrastructure errors under
the existing application database lifecycle.

The response contains exactly `station`, `status`, `observation` and `source`.
`status` is `available`, `stale` or `unavailable`. `observation` is an object
when a usable observation exists and is otherwise `null`. `source` is an object
when a post has been selected and otherwise `null`.

Complete example using the real Val Thorens 6-minute values audited on
2026-10-13, evaluated at 11:05 UTC:

```json
{
  "station": {
    "slug": "val-thorens",
    "name": "Val Thorens"
  },
  "status": "available",
  "observation": {
    "temperature_c": 10.4,
    "humidity_percent": null,
    "wind_direction_deg": null,
    "wind_speed": null,
    "wind_speed_unit": null,
    "precipitation": 0.0,
    "precipitation_unit": null,
    "precipitation_period_minutes": 6,
    "snow_depth_cm": 0.0,
    "ground_state": null,
    "validity_time": "2026-10-13T11:00:00Z",
    "reference_time": "2026-10-13T11:03:07Z",
    "insert_time": "2026-10-13T11:01:41Z",
    "age_minutes": 5.0,
    "fresh": true,
    "raw": {
      "sss": 0.0,
      "t": 283.55,
      "u": null,
      "dd": null,
      "ff": null,
      "etat_sol": null,
      "rr_per": 0.0,
      "rr1": null
    },
    "field_presence": {
      "sss": "value",
      "t": "value",
      "u": "null",
      "dd": "null",
      "ff": "null",
      "etat_sol": "null",
      "rr_per": "value",
      "rr1": "absent",
      "validity_time": "value",
      "reference_time": "value",
      "insert_time": "value"
    }
  },
  "source": {
    "provider": "Météo-France",
    "station_id": "73257005",
    "station_name": "VAL THORENS_SAPC",
    "station_altitude_m": 2277,
    "distance_km": 0.472,
    "is_manual_override": true,
    "endpoint": "infrahoraire-6m",
    "cache_fallback": false
  }
}
```

Observation numeric fields are finite numbers or `null`. `sss` is retained
in meters under `raw.sss` and multiplied by 100 for `snow_depth_cm`.
`t` is retained in kelvins under `raw.t` and reduced by 273.15 for
`temperature_c`. Wind speed is unchanged from `ff`; its unit remains `null`
because no official unit reference is currently present in this repository.
Precipitation units likewise remain unasserted. Precipitation selects `rr_per`
for 6-minute records and `rr1` for hourly records; period is 6 or 60 minutes.
`ground_state` retains the provider code without adding an interpretation.

`raw` contains exactly the eight numeric provider fields shown above, with
missing values rendered as `null`. `field_presence` contains exactly those
eight fields and the three time fields; values are `value`, `null` or `absent`.
Invalid nonnumeric/nonfinite values are normalized to `null`. No missing or
null parameter becomes zero.

All three timestamp outputs are ISO 8601 UTC strings or `null`.
`validity_time` is the actual measurement time. `age_minutes` is computed
from that time, not retrieval time, rounded to two decimals. `fresh` is true
only if unrounded age is between zero and the configured threshold inclusive,
and the response is not an outage fallback. Old data is retained with
`status: "stale"`; future-dated observations are also not considered fresh.

`source.endpoint` is `horaire`, `infrahoraire-6m` or `null` if unavailable.
`source.cache_fallback` is true only for a last-good outage/empty-result fallback.
`station_id` is always a string. `distance_km` is rounded to three decimals
or `null` if Resort coordinates are missing. Other source metadata describes
the selected provider post, not the whole ski area.

Complete unavailable response when the validated post is known:

```json
{
  "station": {"slug": "val-thorens", "name": "Val Thorens"},
  "status": "unavailable",
  "observation": null,
  "source": {
    "provider": "Météo-France",
    "station_id": "73257005",
    "station_name": "VAL THORENS_SAPC",
    "station_altitude_m": 2277,
    "distance_km": 0.472,
    "is_manual_override": true,
    "endpoint": null,
    "cache_fallback": false
  }
}
```

If no post can be selected, the complete response is:

```json
{
  "station": {"slug": "unmapped-resort", "name": "Unmapped Resort"},
  "status": "unavailable",
  "observation": null,
  "source": null
}
```

## Tests

From the backend root, with project dependencies and pytest installed:

```sh
python -m pytest -q tests/test_meteo_france.py
python -m pytest -q tests/test_meteo_france.py tests/test_public_cache.py tests/test_database_lifecycle.py
python -m pytest -q
```

Tests mock provider HTTP and Redis; they do not require a real API key, perform
network calls or alter a production database. Mapping/route tests use isolated
SQLite databases. Live provider values can change; the contract example is the
audited snapshot, not a prediction of future values.

Validation in the execution environment: 47 weather tests plus 20 public-cache
and database-lifecycle tests pass (67 total). The full suite cannot pass here:
existing application-factory tests require PostgreSQL at localhost:5433, where
no server is listening. A public-resort widget timestamp test also fails with
missing `updated_at`. Both failures were reproduced against an isolated copy
of the original Git HEAD, before this integration. The migration has not been
executed against PostgreSQL or production; no deployment was performed.
