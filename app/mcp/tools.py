"""Explicit tools delegating to the validated Station Ops services."""
import json
import logging
from copy import deepcopy

from jsonschema import Draft202012Validator
from mcp.server.auth.middleware.auth_context import get_access_token
from app.services.station_ops_oauth import READ, WRITE, ORIGIN, RESOURCE_METADATA_PATH
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from app.datetime_utils import utcnow
from app.models.resort import Resort
from app.services.station_ops import scan, compare, review, research, apply, catalog
from app.services.station_ops.apply_contract import ApplyError, MAX_APPLY_OPERATIONS
from app.services.station_ops.candidates import ComparePayloadError, MAX_BODY_BYTES, MAX_CANDIDATES
from app.services.station_ops.schema import SchemaCompatibilityError
from app.services.station_ops.research_schema import RESEARCH_VERSION, research_schema

logger = logging.getLogger('station_ops.mcp.audit')
logger.setLevel(logging.INFO)
MACHINE_ACTOR = 'station_ops_mcp'


def _object(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


def tool_definitions():
    candidates = {'type': 'array', 'items': {'type': 'object'}, 'minItems': 1, 'maxItems': MAX_CANDIDATES}
    decisions = {'type': 'array', 'items': {'type': 'object'}}
    compare_input = _object({'candidates': candidates}, ('candidates',))
    review_input = _object({'candidates': candidates, 'decisions': decisions}, ('candidates',))
    apply_fields = {'candidates': candidates, 'decisions': decisions, 'plan_fingerprint': {'type': 'string'}}
    specs = [
        ('station_scan', 'Read the current Station Ops snapshot. Filter values use the existing SCAN string contract.',
         _object({key: {'type': 'string'} for key in scan.FILTER_FIELDS})),
        ('station_catalog', 'Read a compact catalogue page for large audits. Use next_offset with identical filters; default limit 100, maximum 250. No editorial content or collections.',
         _object({**{key: {'type': 'string'} for key in catalog.CATALOG_FILTERS},
                  'limit': {'type': 'integer', 'minimum': 1, 'maximum': catalog.MAX_LIMIT, 'default': catalog.DEFAULT_LIMIT},
                  'offset': {'type': 'integer', 'minimum': 0, 'maximum': catalog.MAX_OFFSET, 'default': 0}})),
        ('research_contract', 'Read the canonical RESEARCH 1.0 schema and shared limits. No web research.', _object({})),
        # Structural/semantic invalid research must reach its validator and retain its diagnostics.
        ('research_validate', 'Validate externally collected RESEARCH 1.0 and return the exact COMPARE payload. No web research.',
         {'type': 'object'}),
        ('compare', 'Compare original candidates against the current database; read only.', compare_input),
        ('review', 'Recompute COMPARE and prepare explicit per-operation decisions; no writes.', review_input),
        ('apply_dry_run', 'Validate the approved REVIEW plan, forcing dry_run. Cannot commit.',
         _object(apply_fields, ('candidates', 'decisions', 'plan_fingerprint'))),
        ('apply_commit', 'WRITE: execute explicitly approved operations through all existing APPLY protections. Requires server kill switch and confirm_apply=true.',
         _object({**apply_fields, 'confirm_apply': {'type': 'boolean'}},
                 ('candidates', 'decisions', 'plan_fingerprint', 'confirm_apply'))),
    ]
    return [Tool(name=name, description=description, inputSchema=schema, outputSchema={'type': 'object'},
                 securitySchemes=[{'type': 'oauth2', 'scopes': [READ, WRITE] if name == 'apply_commit' else [READ]}],
                 _meta={'securitySchemes': [{'type': 'oauth2', 'scopes': [READ, WRITE] if name == 'apply_commit' else [READ]}]},
                 annotations=ToolAnnotations(readOnlyHint=name != 'apply_commit',
                                             destructiveHint=name == 'apply_commit',
                                             idempotentHint=name != 'apply_commit', openWorldHint=False))
            for name, description, schema in specs]


class ToolRefusal(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def research_contract():
    schema = research_schema()
    candidate = schema['$defs']['station_research_candidate']['properties']
    source = candidate['field_sources']['additionalProperties']['items']['properties']
    return {'research_version': RESEARCH_VERSION,
            'field_statuses': candidate['field_statuses']['properties']['name']['enum'],
            'source_types': source['source_type']['enum'],
            'research_levels': candidate['research_level']['enum'],
            'candidate_fields': candidate['target_fields']['items']['enum'],
            'limits': {'max_candidates': MAX_CANDIDATES, 'max_body_bytes': MAX_BODY_BYTES,
                       'max_apply_commit_operations': MAX_APPLY_OPERATIONS},
            'json_schema': schema}


def _dispatch(name, arguments):
    if name == 'station_scan':
        try:
            scan.parse_filters(arguments)
        except ValueError as exc:
            raise ToolRefusal('invalid_filters', str(exc)) from exc
        return scan.scan_stations(arguments)
    if name == 'station_catalog':
        return catalog.catalog_stations(arguments)
    if name == 'research_contract':
        return research_contract()
    if name == 'research_validate':
        return research.validate_research(arguments)
    if name == 'compare':
        return compare.compare_candidates(arguments)
    if name == 'review':
        return review.review_candidates(arguments)
    if name in ('apply_dry_run', 'apply_commit'):
        # Never accept mode from the client. Schema validation rejects it first.
        payload = deepcopy(arguments)
        payload['mode'] = 'commit' if name == 'apply_commit' else 'dry_run'
        if name == 'apply_commit' and not any(
                isinstance(row, dict) and isinstance(row.get('operations'), dict) and
                any(value == 'approved' for value in row['operations'].values())
                for row in payload['decisions']):
            raise ToolRefusal('explicit_review_approval_required', 'Commit requires explicit operation approvals from REVIEW')
        return apply.apply_candidates(payload, actor_id=MACHINE_ACTOR)
    raise ToolRefusal('unknown_tool', 'Unknown Station Ops tool', 404)


def invoke(flask_app, name, arguments):
    """Synchronous worker boundary: app context and connection cleanup, no transaction wrapper."""
    outcome, execution_id, count, failed = 'error', None, 0, False
    token = get_access_token()
    try:
        definitions = {tool.name: tool for tool in tool_definitions()}
        if name not in definitions:
            raise ToolRefusal('unknown_tool', 'Unknown Station Ops tool', 404)
        if token is not None and not set([READ, WRITE] if name == 'apply_commit' else [READ]) <= set(token.scopes):
            raise ToolRefusal('insufficient_scope', 'OAuth scopes do not authorize this tool', 403)
        if not isinstance(arguments, dict):
            raise ToolRefusal('invalid_tool_arguments', 'Expected an arguments object')
        rows = arguments.get('candidates')
        count = len(rows) if isinstance(rows, list) else 0
        if count > MAX_CANDIDATES:
            raise ComparePayloadError('Batch exceeds 1000 candidates', 413)
        try:
            body = json.dumps(arguments, allow_nan=False, separators=(',', ':')).encode()
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise ToolRefusal('invalid_tool_arguments', 'Expected finite UTF-8 JSON arguments') from exc
        if len(body) > MAX_BODY_BYTES:
            raise ComparePayloadError('Tool arguments exceed 16 MiB', 413)
        if next(Draft202012Validator(definitions[name].inputSchema).iter_errors(arguments), None) is not None:
            raise ToolRefusal('invalid_tool_arguments', 'Arguments do not match the tool input schema')
        database = Resort._meta.database
        was_closed = database.is_closed()
        with flask_app.app_context():
            try:
                result = _dispatch(name, arguments)
            finally:
                # Services own isolation/transactions; close only this worker's connection.
                if was_closed and not database.is_closed():
                    database.close()
        execution_id = result.get('execution_id')
        if name == 'research_validate' and not result['valid']:
            result = {**result, 'code': 'invalid_research_payload', 'message': 'RESEARCH validation failed', 'status': 400}
            failed = True
        outcome = 'refused' if failed else 'success'
    except ApplyError as exc:
        execution_id = exc.execution_id
        result = {'code': exc.code, 'message': str(exc), 'status': exc.status,
                  **exc.details, **({'execution_id': execution_id} if execution_id else {})}
        failed, outcome = True, 'refused'
    except review.ReviewDecisionError as exc:
        result = {'code': 'invalid_review_decisions', 'message': 'Invalid REVIEW decisions', 'status': 409, 'issues': exc.issues}
        failed, outcome = True, 'refused'
    except SchemaCompatibilityError as exc:
        execution_id = getattr(exc, 'execution_id', None)
        result = {'code': 'station_ops_schema_incompatible', 'message': str(exc), 'status': 503,
                  'schema_findings': exc.findings, **({'execution_id': execution_id} if execution_id else {})}
        failed, outcome = True, 'refused'
    except (ComparePayloadError, ToolRefusal) as exc:
        result = {'code': getattr(exc, 'code', 'invalid_station_ops_payload'), 'message': str(exc), 'status': exc.status}
        failed, outcome = True, 'refused'
    except Exception as exc:
        execution_id = getattr(exc, 'execution_id', None)
        # Exception strings can contain SQL parameters or editorial text. Never log them.
        result = {'code': 'station_ops_mcp_failed', 'message': 'Station Ops tool failed', 'status': 500,
                  **({'execution_id': execution_id} if execution_id else {})}
        failed = True
    finally:
        # Untrusted tool names are not logged; unknown calls get a fixed label.
        safe_name = name if name in {t.name for t in tool_definitions()} else 'unknown'
        logger.info(json.dumps({'event': 'station_ops_mcp', 'timestamp': utcnow().isoformat(),
                               'tool': safe_name, 'result': outcome, 'candidate_count': count,
                               'admin_id': token.subject if token else None,
                               'execution_id': execution_id}, sort_keys=True))
    auth_meta = None
    if result.get('code') == 'insufficient_scope':
        auth_meta = {'mcp/www_authenticate': [
            f'Bearer resource_metadata="{ORIGIN + RESOURCE_METADATA_PATH}", scope="{READ} {WRITE}"']}
    return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False, separators=(',', ':')))],
                          structuredContent=result, isError=failed, _meta=auth_meta)
