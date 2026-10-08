"""Canonical JSON Schema generated from Station Ops' existing field catalog."""
from .candidates import CANDIDATE_FIELDS, SCALAR_FIELDS, COLLECTION_FIELDS, MAX_CANDIDATES
from .normalization import NUMERIC_FIELDS, INTEGER_FIELDS

RESEARCH_VERSION = "1.0"
SOURCE_TYPES = ('official', 'government', 'ski_area_official', 'tourism_official',
                'map_reference', 'reputable_reference', 'secondary')
FIELD_STATES = ('not_researched', 'not_found', 'found', 'conflicting', 'ambiguous')


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def text(maximum=None):
    schema = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}
    if maximum:
        schema['maxLength'] = maximum
    return schema


def research_schema():
    fields = sorted(CANDIDATE_FIELDS)
    data = {field: text() for field in SCALAR_FIELDS}
    data.update({field: {'type': 'integer' if field in INTEGER_FIELDS else 'number'} for field in NUMERIC_FIELDS})
    data['is_active'] = {'type': 'boolean'}
    data['page_layout_version'] = {'enum': ['legacy', 'v2']}
    data.update({field: {'type': 'array', 'items': {'type': 'object'}} for field in COLLECTION_FIELDS})
    data['widgets'] = {'type': 'object'}
    area = obj({'id': {'type': 'integer', 'minimum': 1, 'maximum': 9223372036854775807},
                'slug': text(), 'name': text()})
    area['minProperties'] = 1
    data['ski_areas'] = {'type': 'array', 'items': area, 'minItems': 1}
    source = obj({'url': text(), 'source_type': {'enum': list(SOURCE_TYPES)},
                  'publisher': text(256), 'observed_at': text(), 'value_observed': {}}, ('url', 'source_type'))
    candidate = obj({
        'client_ref': text(256),
        'identity': obj({'kind': {'enum': ['existing', 'discovered']},
                         'status': {'enum': ['resolved', 'ambiguous', 'unresolved']}}, ('kind', 'status')),
        'research_level': {'enum': ['identity', 'core', 'extended']},
        'target_fields': {'type': 'array', 'items': {'enum': fields}, 'minItems': 1, 'uniqueItems': True},
        'data': obj(data),
        'field_statuses': obj({field: {'enum': list(FIELD_STATES)} for field in fields}),
        'field_sources': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': source}},
        'relation_coverage': obj({'ski_areas': {'enum': ['complete']}}),
        'notes': {'type': 'array', 'items': text()},
    }, ('client_ref', 'identity', 'research_level', 'target_fields', 'data', 'field_statuses'))
    scope = obj({'type': {'enum': ['station', 'region', 'country']}, 'country_code': {'type': 'string', 'pattern': '^[A-Z]{2}$'},
                 'id': text(), 'slug': text(), 'name': text(), 'region_id': text(), 'region_name': text()},
                ('type', 'country_code'))
    root = obj({'research_version': {'const': RESEARCH_VERSION}, 'scope': scope,
                'batch': obj({'index': {'type': 'integer', 'minimum': 1},
                              'total': {'type': 'integer', 'minimum': 1}}, ('index', 'total')),
                'candidates': {'type': 'array', 'minItems': 1, 'maxItems': MAX_CANDIDATES,
                               'items': {'$ref': '#/$defs/station_research_candidate'}}},
               ('research_version', 'scope', 'candidates'))
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'Station research batch', **root,
            '$defs': {'station_research_candidate': candidate}}
