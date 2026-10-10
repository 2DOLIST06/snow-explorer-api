"""Compact, read-only catalogue pages for external RESEARCH orchestration."""
from functools import reduce
from operator import or_

from peewee import Value, fn

from app.datetime_utils import utcnow
from app.models.resort import Resort
from app.models.ski_area import SkiAreaResort
from .candidates import ComparePayloadError
from .scan import SCHEMA_VERSION, parse_filters, read_only_scan, _json_value
from .schema import PhysicalSchema

CATALOG_VERSION = '1.0'
DEFAULT_LIMIT = 100
MAX_LIMIT = 250
MAX_OFFSET = 9223372036854775807
CATALOG_FILTERS = ('country_code', 'region_id', 'department', 'is_active', 'ski_area_id')
CATALOG_FIELDS = ('id', 'name', 'slug', 'is_active', 'country_code', 'region_id', 'region_name',
                  'department', 'latitude', 'longitude', 'altitude_min_m', 'altitude_max_m',
                  'ski_area_km', 'website_url', 'updated_at')
MEDIA_FIELDS = {'has_cover_image': ('cover_image_url',), 'has_logo': ('logo_url',),
                'has_piste_map': ('pistes_small_map_url', 'pistes_large_map_url')}


def catalog_stations(params=None):
    params = {} if params is None else params
    if not isinstance(params, dict) or set(params) - set(CATALOG_FILTERS) - {'limit', 'offset'}:
        raise ComparePayloadError('Unknown or invalid station_catalog arguments')
    limit, offset = params.get('limit', DEFAULT_LIMIT), params.get('offset', 0)
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ComparePayloadError('limit must be an integer between 1 and 250')
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ComparePayloadError('offset must be a non-negative bigint integer')
    try:
        filters = parse_filters({key: value for key, value in params.items() if key in CATALOG_FILTERS})
    except ValueError as exc:
        raise ComparePayloadError(str(exc)) from exc
    with read_only_scan(Resort._meta.database):
        return _catalog(filters, limit, offset)


def _catalog(filters, limit, offset):
    models = (Resort, SkiAreaResort) if 'ski_area_id' in filters else (Resort,)
    schema = PhysicalSchema(Resort._meta.database, models)
    schema.require(Resort, 'id')
    base = Resort.select(Resort.id)
    for key, value in filters.items():
        if key == 'ski_area_id':
            schema.require(SkiAreaResort, 'resort', 'ski_area')
            base = base.where(Resort.id.in_(SkiAreaResort.select(SkiAreaResort.resort)
                                          .where(SkiAreaResort.ski_area == value)))
        else:
            schema.require(Resort, key)
            base = base.where(getattr(Resort, key) == value)
    # Only selected physical columns are read. Missing optional columns are
    # null with diagnostics; physical fractional numerics retain their precision.
    columns = []
    for name in CATALOG_FIELDS:
        if schema.has(Resort, name):
            field = getattr(Resort, name)
            columns.append(field.coerce(False) if (Resort, name) in schema.raw_numeric_fields else field)
    incomplete_media = set()
    for flag, names in MEDIA_FIELDS.items():
        available = [name for name in names if schema.has(Resort, name)]
        if len(available) != len(names):
            incomplete_media.add(flag)
        present = [(getattr(Resort, name).is_null(False) & (fn.LENGTH(fn.TRIM(getattr(Resort, name))) > 0))
                   for name in available]
        columns.append((fn.COALESCE(reduce(or_, present), False) if present else Value(False)).alias(flag))
    total = base.count()
    query = base.select(*columns).order_by(Resort.id).limit(limit).offset(offset).dicts()
    stations = []
    for raw in query:
        row = {name: _json_value(raw.get(name)) for name in CATALOG_FIELDS}
        for flag in MEDIA_FIELDS:
            row[flag] = True if raw[flag] else None if flag in incomplete_media else False
        stations.append(row)
    returned = len(stations)
    has_more = offset + returned < total
    relevant = set(CATALOG_FIELDS) | {name for names in MEDIA_FIELDS.values() for name in names}
    findings = [item for item in schema.findings if item.get('field') in relevant or item.get('table') != Resort._meta.table_name]
    return {'schema_version': SCHEMA_VERSION, 'catalog_version': CATALOG_VERSION,
            'generated_at': utcnow().isoformat(), 'scope': {'filters': filters},
            'order_by': 'id', 'limit': limit, 'offset': offset,
            'total': total, 'returned': returned, 'has_more': has_more,
            'next_offset': offset + returned if has_more else None,
            'stations': stations, 'schema_findings': findings}
