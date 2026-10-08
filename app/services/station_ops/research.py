"""Pure external RESEARCH validation/conversion. No HTTP or database access."""
from copy import deepcopy
from collections import Counter
import json
import math
import re

from .candidates import MAX_BODY_BYTES, MAX_CANDIDATES, ComparePayloadError, prepare_candidate, _sources_valid, SCALAR_FIELDS, COLLECTION_FIELDS
from .normalization import normalize, json_normalized
from .research_schema import RESEARCH_VERSION, research_schema


def _check(value, schema, root, path, errors):
    """Validate the small explicit JSON Schema vocabulary used by our contract."""
    if '$ref' in schema:
        schema = root['$defs'][schema['$ref'].rsplit('/', 1)[1]]
    def error(code, message):
        errors.append({'path': path, 'code': code, 'message': message})
    kind = schema.get('type')
    valid_type = {'object': isinstance(value, dict), 'array': isinstance(value, list),
                  'string': isinstance(value, str), 'boolean': type(value) is bool,
                  'number': type(value) in (int, float), 'integer': type(value) is int or (type(value) is float and math.isfinite(value) and value.is_integer())}
    if kind and not valid_type[kind]:
        error('type_invalid', f'Expected {kind}')
        return
    if 'const' in schema and value != schema['const']:
        error('version_invalid', 'Unsupported research version')
    if 'enum' in schema and value not in schema['enum']:
        error('enum_invalid', 'Value is outside the documented choices')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', len(value)) or ('pattern' in schema and not re.search(schema['pattern'], value)):
            error('text_invalid', 'Text is empty, too long or malformed')
    if type(value) in (int, float):
        if (type(value) is float and not math.isfinite(value)) or value < schema.get('minimum', value) or value > schema.get('maximum', value):
            error('number_invalid', 'Number is non-finite or outside its range')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                errors.append({'path': f'{path}.{key}', 'code': 'required', 'message': 'Required field is missing'})
        if len(value) < schema.get('minProperties', 0):
            error('object_empty', 'At least one reference is required')
        for key, item in value.items():
            props = schema.get('properties', {})
            extra = schema.get('additionalProperties', True)
            if key in props:
                _check(item, props[key], root, f'{path}.{key}', errors)
            elif extra is False:
                errors.append({'path': f'{path}.{key}', 'code': 'unknown_field', 'message': 'Unknown field'})
            elif isinstance(extra, dict):
                _check(item, extra, root, f'{path}.{key}', errors)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', len(value)):
            error('batch_or_list_size_invalid', 'Array size is outside its limits')
        if schema.get('uniqueItems') and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            error('duplicate_item', 'Array entries must be unique')
        for index, item in enumerate(value):
            _check(item, schema.get('items', {}), root, f'{path}[{index}]', errors)


def validate_research(payload):
    """Return diagnostics and an exact COMPARE envelope, never implicit clears."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ComparePayloadError('Expected finite UTF-8 JSON') from exc
    if len(encoded) > MAX_BODY_BYTES:
        raise ComparePayloadError('RESEARCH body exceeds 16 MiB', 413)
    if isinstance(payload, dict) and isinstance(payload.get('candidates'), list) and len(payload['candidates']) > MAX_CANDIDATES:
        raise ComparePayloadError(f'Batch exceeds {MAX_CANDIDATES} candidates', 413)
    errors, warnings, results, exported, excluded = [], [], [], [], []
    schema = research_schema()
    try:
        _check(payload, schema, schema, '$', errors)
    except RecursionError as exc:
        raise ComparePayloadError('JSON nesting exceeds validation limits') from exc
    def issue(target, path, code, message):
        target.append({'path': path, 'code': code, 'message': message})
    if not errors:
        scope = payload['scope']
        if scope['type'] == 'region' and not (scope.get('region_id') or scope.get('region_name')):
            issue(errors, '$.scope', 'scope_identity_missing', 'Region scope needs region_id or region_name')
        if scope['type'] == 'station' and not any(scope.get(k) for k in ('id', 'slug', 'name')):
            issue(errors, '$.scope', 'scope_identity_missing', 'Station scope needs id, slug or name')
        if payload.get('batch', {}).get('index', 1) > payload.get('batch', {}).get('total', 1):
            issue(errors, '$.batch', 'batch_index_invalid', 'index must not exceed total')
        seen = set()
        for index, row in enumerate(payload['candidates']):
            path = f'$.candidates[{index}]'
            ref, data = row['client_ref'], row['data']
            sources = row.get('field_sources', {})
            targets = row['target_fields']
            states = {field: row['field_statuses'].get(field, 'not_researched') for field in targets}
            if ref in seen:
                issue(errors, path + '.client_ref', 'duplicate_client_ref', 'client_ref must be unique within a batch')
            seen.add(ref)
            if (set(data) | set(row['field_statuses'])) - set(targets):
                issue(errors, path, 'field_outside_targets', 'Data and statuses must belong to target_fields')
            if not _sources_valid(sources):
                issue(errors, path + '.field_sources', 'field_sources_invalid', 'Invalid provenance path, URL, timestamp or metadata')
            for field in targets:
                state = states[field]
                if (field in data) != (state == 'found'):
                    issue(errors, path + '.data.' + field, 'field_state_mismatch', 'Only found fields have proposed data; found fields require data')
                if state == 'found' and not sources.get(field):
                    issue(warnings, path + '.field_sources.' + field, 'source_absent', 'No field-level source supplied')
                if state in ('conflicting', 'ambiguous'):
                    issue(warnings, path + '.field_statuses.' + field, 'unresolved_field_excluded', 'Unresolved field is excluded from COMPARE data')
                # Contradictory machine-readable scalar observations cannot silently become one proposal.
                values = set()
                if field in SCALAR_FIELDS:
                    for source in sources.get(field, []):
                        if 'value_observed' in source:
                            try:
                                values.add(json.dumps(json_normalized(normalize(field, source['value_observed'])), sort_keys=True))
                            except (ValueError, TypeError, UnicodeError, OverflowError):
                                issue(warnings, path + '.field_sources.' + field, 'observation_not_comparable', 'Raw observation retained; not comparable as a scalar value')
                if len(values) > 1 and state != 'conflicting':
                    issue(errors, path + '.field_statuses.' + field, 'source_conflict_not_declared', 'Contradictory scalar observations require conflicting status')
            if row['identity']['kind'] == 'discovered' and 'id' in data:
                issue(errors, path + '.data.id', 'discovered_internal_id_forbidden', 'Discovered stations cannot supply an internal id')
            if 'country_code' in data and data['country_code'].upper() != scope['country_code']:
                issue(errors, path + '.data.country_code', 'country_scope_mismatch', 'Candidate country differs from research scope')
            if 'ski_areas' in data and row.get('relation_coverage', {}).get('ski_areas') != 'complete':
                issue(errors, path + '.relation_coverage', 'relation_coverage_required', 'ski_areas must describe the complete desired relation set')
            for field in sorted(set(data) & COLLECTION_FIELDS):
                issue(warnings, path + '.data.' + field, 'collection_comparison_not_supported', 'COMPARE keeps this collection blocked for review')
            if 'ski_areas' in data:
                issue(warnings, path + '.data.ski_areas', 'relation_set_replacement', 'Omitted existing relations may be proposed for removal; REVIEW requires explicit approval')
            ready = row['identity']['status'] == 'resolved' and any(data.get(k) for k in ('id', 'slug', 'name'))
            candidate = {'client_ref': ref, 'data': deepcopy(data), 'field_sources': deepcopy(sources)}
            prepared = prepare_candidate(candidate)
            for error in prepared['result']['validation']['errors']:
                if error['code'] != 'identity_missing' or ready:
                    issue(errors, path + '.data', error['code'], 'Candidate is incompatible with COMPARE')
            warnings.extend({**warning, 'path': path + '.data'} for warning in prepared['result']['validation']['warnings'])
            if ready:
                exported.append(candidate)
            else:
                excluded.append({'client_ref': ref, 'reason': 'identity_unresolved'})
                issue(warnings, path + '.identity', 'identity_unresolved', 'Candidate is excluded until identity is resolved')
            if row['identity']['status'] == 'ambiguous':
                status = 'identity_ambiguous'
            elif not ready or (row['identity']['kind'] == 'discovered' and not all(data.get(k) for k in ('name', 'slug'))):
                status = 'insufficient'
            elif any(state in ('conflicting', 'ambiguous') for state in states.values()):
                status = 'conflict'
            else:
                status = 'complete' if all(state == 'found' for state in states.values()) else 'partial'
            results.append({'client_ref': ref, 'audit': {'research_status': status, 'research_level': row['research_level'],
                'fields_checked': sorted(k for k, v in states.items() if v != 'not_researched'),
                **{'fields_' + label: sorted(k for k, v in states.items() if v == state)
                   for label, state in [('found', 'found'), ('not_found', 'not_found'), ('conflicting', 'conflicting'),
                                        ('ambiguous', 'ambiguous'), ('not_researched', 'not_researched')]},
                'notes': deepcopy(row.get('notes', []))}, 'compare_eligible': ready})
    valid = not errors
    return {'research_version': RESEARCH_VERSION, 'valid': valid, 'errors': errors, 'warnings': warnings,
            'summary': {'total_candidates': len(payload.get('candidates', [])) if isinstance(payload, dict) and isinstance(payload.get('candidates'), list) else 0,
                        **dict(Counter(r['audit']['research_status'] for r in results)),
                        'compare_candidates': len(exported) if valid else 0, 'excluded_candidates': len(excluded)},
            'results': results, 'excluded_candidates': excluded,
            'compare_payload': {'candidates': exported} if valid and exported else None}
