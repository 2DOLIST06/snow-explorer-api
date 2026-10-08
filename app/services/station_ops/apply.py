"""Server-recomputed APPLY: read-only dry run or one atomic, locked commit."""
from contextlib import contextmanager
import hmac
import json
import logging
import os
import re
import uuid

from peewee import IntegrityError, OperationalError, PostgresqlDatabase, SqliteDatabase
from flask import current_app, has_app_context

from app.datetime_utils import utcnow
from app.models.resort import Resort
from app.models.ski_area import SkiArea, SkiAreaResort
from app.services.public_cache import bump_public_resorts_version, invalidate_station
from .apply_contract import APPLY_VERSION, MAX_APPLY_OPERATIONS, ApplyError
from .apply_execution import execute_plan, quoted_table, verify_written
from .apply_validation import validate_plan
from .candidates import ComparePayloadError
from .operations import fingerprint
from .review import ReviewDecisionError, review_in_transaction
from .scan import read_only_scan

audit_logger = logging.getLogger('station_ops.apply.audit')
audit_logger.setLevel(logging.INFO)


def _payload(payload):
    allowed = {'candidates', 'decisions', 'plan_fingerprint', 'mode', 'confirm_apply'}
    if not isinstance(payload, dict) or set(payload) - allowed or not {'candidates', 'plan_fingerprint'} <= set(payload):
        raise ApplyError('invalid_apply_payload', 'Expected original candidates, decisions and a REVIEW fingerprint; no operations accepted')
    mode = payload.get('mode', 'dry_run')
    if mode not in ('dry_run', 'commit'):
        raise ApplyError('invalid_apply_mode', 'mode must be dry_run or commit')
    # Fail closed, before confirmation, recomputation or any business transaction.
    # Read per request so this server switch cannot be overridden by a payload.
    if mode == 'commit' and os.environ.get('STATION_OPS_APPLY_COMMIT_ENABLED', '').strip().lower() not in {'true', '1', 'yes', 'on'}:
        raise ApplyError('station_ops_apply_commit_disabled',
                         'Station Ops APPLY commit is disabled on this environment.', 403)
    if 'confirm_apply' in payload and type(payload['confirm_apply']) is not bool:
        raise ApplyError('apply_confirmation_required', 'confirm_apply must be a JSON boolean')
    if mode == 'commit' and payload.get('confirm_apply') is not True:
        raise ApplyError('apply_confirmation_required', 'commit requires confirm_apply: true')
    supplied = payload['plan_fingerprint']
    if not isinstance(supplied, str) or not re.fullmatch(r'[0-9a-f]{64}', supplied):
        raise ApplyError('invalid_plan_fingerprint', 'Expected the lowercase SHA-256 fingerprint from REVIEW')
    return mode, supplied, {key: payload[key] for key in ('candidates', 'decisions') if key in payload}


@contextmanager
def commit_transaction(database):
    if database.in_transaction():
        raise RuntimeError('APPLY requires a fresh transaction')
    if isinstance(database, PostgresqlDatabase):
        with database.atomic(isolation_level='SERIALIZABLE'):
            database.execute_sql("SET LOCAL lock_timeout = '5s'")
            database.execute_sql("SET LOCAL statement_timeout = '30s'")
            # Coarse V1 lock deliberately protects absence and fuzzy matching
            # against ALL ordinary SQL writers, including legacy admin routes.
            # Acquired before the first snapshot/data SELECT, held until COMMIT.
            database.execute_sql('LOCK TABLE ' + quoted_table(Resort) + ' IN SHARE ROW EXCLUSIVE MODE')
            # Membership gap protection: row locks cannot lock absent relations.
            database.execute_sql('LOCK TABLE ' + quoted_table(SkiAreaResort) + ' IN SHARE ROW EXCLUSIVE MODE')
            yield
    elif isinstance(database, SqliteDatabase):
        # Test fixtures only: BEGIN IMMEDIATE prevents another writer intervening.
        with database.atomic('IMMEDIATE'):
            yield
    else:
        raise RuntimeError('Unsupported APPLY database')


def lock_targets(database, operations):
    if not isinstance(database, PostgresqlDatabase) or not operations:
        return
    station_ids = sorted({op['target_id'] for op in operations
                          if op['operation'] != 'create_station' and not op.get('depends_on') and op['target_id'] is not None})
    area_ids = sorted({op['related_id'] for op in operations if op['operation'].endswith('ski_area_relation')})
    if station_ids:
        list(Resort.select(Resort.id).where(Resort.id.in_(station_ids)).order_by(Resort.id).for_update().dicts())
    if area_ids:
        # Also protects the referenced domains from edits/deletion until COMMIT.
        list(SkiArea.select(SkiArea.id).where(SkiArea.id.in_(area_ids)).order_by(SkiArea.id).for_update().dicts())
    if station_ids and area_ids:
        list(SkiAreaResort.select(SkiAreaResort.resort, SkiAreaResort.ski_area)
             .where(SkiAreaResort.resort.in_(station_ids), SkiAreaResort.ski_area.in_(area_ids))
             .order_by(SkiAreaResort.resort, SkiAreaResort.ski_area).for_update().dicts())


def _audit(execution_id, actor_id, mode, outcome, plan_fingerprint=None, operations=(), error_code=None):
    # Hashes/lengths, identifiers and counts only; never content, URL, cookie,
    # passwords, CSRF or request/SQL parameter dumps.
    if has_app_context() and not audit_logger.handlers:
        for handler in current_app.logger.handlers:
            audit_logger.addHandler(handler)
    event = {'event': 'station_ops_apply', 'timestamp': utcnow().isoformat(),
             'execution_id': execution_id, 'admin_id': actor_id, 'mode': mode,
             'outcome': outcome, 'plan_fingerprint': plan_fingerprint, 'error_code': error_code,
             'operation_count': len(operations)}
    audit_logger.info(json.dumps(event, sort_keys=True))
    # One bounded record per action, rather than a huge batch log line that a
    # production log collector could truncate.
    for op in operations:
        detail = {**event, 'event': 'station_ops_apply_operation',
                             'operation_id': op['operation_id'], 'target_id': op['target_id'],
                             'operation': op['operation'], 'field': op['field'],
                             'related_id': op.get('related_id'), 'source_count': op['source_count'],
                             'existing_fingerprint': fingerprint(op['existing']),
                             'candidate_fingerprint': fingerprint(op.get('candidate_input', op['candidate']))}
        audit_logger.info(json.dumps(detail, sort_keys=True))


def _sqlstate(exc):
    # Peewee may expose the psycopg exception in args, cause or context.
    seen, pending = set(), [exc]
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        code = getattr(item, 'pgcode', None) or getattr(item, 'sqlstate', None)
        if code:
            return code
        pending.extend(x for x in (getattr(item, 'orig', None), getattr(item, '__cause__', None), getattr(item, '__context__', None),
                                   *getattr(item, 'args', ())) if isinstance(x, BaseException))
    return None


def apply_candidates(payload, *, actor_id=None):
    execution_id, mode, supplied, operations = str(uuid.uuid4()), 'unknown', None, []
    try:
        mode, supplied, review_payload = _payload(payload)
        database = Resort._meta.database
        _audit(execution_id, actor_id, mode, 'attempted', supplied)
        transaction = read_only_scan(database) if mode == 'dry_run' else commit_transaction(database)
        def build(reviewed, prepared, active_database, context):
            nonlocal operations
            if reviewed['summary']['invalid']:
                raise ApplyError('invalid_apply_candidates', 'Invalid candidates must be corrected before APPLY',
                                 issues=[{'client_ref': row['client_ref'], 'validation': row['validation']}
                                         for row in reviewed['results'] if row['status'] == 'invalid'])
            plan = reviewed['apply_plan']
            if not hmac.compare_digest(supplied, plan['plan_fingerprint']):
                raise ApplyError('plan_fingerprint_mismatch', 'Plan changed; repeat REVIEW and approval', 409,
                                 provided_plan_fingerprint=supplied, recomputed_plan_fingerprint=plan['plan_fingerprint'])
            operations = plan['operations']
            if mode == 'commit' and len(operations) > MAX_APPLY_OPERATIONS:
                raise ApplyError('apply_operations_limit', 'Commit exceeds 1000 approved operations; split the batch', 413,
                                 max_apply_operations=MAX_APPLY_OPERATIONS)
            if mode == 'commit':
                lock_targets(active_database, operations)
            validated = validate_plan(operations, prepared, active_database, context)
            targets, slugs = {}, []
            if mode == 'commit' and operations:
                executed = execute_plan(active_database, validated)
                slugs = verify_written(validated, executed)
                targets = executed['targets']
                operations = [{**op, 'target_id': targets.get(op['client_ref'], op['target_id'])} for op in operations]
                # This event is provisional until the following COMMIT succeeds.
                _audit(execution_id, actor_id, mode, 'verified_pending_commit', supplied, operations)
            count = len(operations)
            result = {**{key: reviewed[key] for key in ('schema_version', 'compare_version', 'review_version',
                                                       'schema_findings', 'catalog_findings')},
                      'apply_version': APPLY_VERSION, 'generated_at': utcnow().isoformat(), 'mode': mode,
                      'execution_id': execution_id, 'plan_fingerprint': supplied,
                      'summary': {'approved_operations': count, 'validated_operations': count,
                                  'written_operations': count if mode == 'commit' else 0,
                                  'applied_operations': count if mode == 'commit' else 0},
                      'operations_ready': count,
                      'operations': [{'operation_id': op['operation_id'], 'status': 'ready' if mode == 'dry_run' else 'applied',
                                      'target_id': targets.get(op['client_ref'], op['target_id']),
                                      'client_ref': op['client_ref'], 'operation': op['operation'], 'field': op['field']}
                                     for op in validated['ordered']]}
            result['would_apply' if mode == 'dry_run' else 'applied'] = bool(count)
            if not count:
                result['reason'] = 'no_approved_operations'
            return result, slugs
        with transaction:
            result, slugs = review_in_transaction(review_payload, result_builder=build)
    except ReviewDecisionError as exc:
        error = ApplyError('stale_precondition', 'Decisions no longer match REVIEW; repeat REVIEW and approval', 409, issues=exc.issues)
        error.execution_id = execution_id
        _audit(execution_id, actor_id, mode, 'refused', supplied, error_code=error.code)
        raise error from exc
    except (IntegrityError, OperationalError) as exc:
        exc.execution_id = execution_id
        state = _sqlstate(exc)
        if isinstance(exc, IntegrityError):
            error = ApplyError('constraint_conflict', 'Physical database constraint rejected the plan; all business changes rolled back', 409)
        elif state in {'40001', '40P01', '55P03', '57014'} or isinstance(Resort._meta.database, SqliteDatabase) and 'locked' in str(exc).lower():
            error = ApplyError('concurrent_conflict', 'Concurrent write or timeout; repeat REVIEW; all business changes rolled back', 409)
        else:
            _audit(execution_id, actor_id, mode, 'failed', supplied, operations, 'unexpected_database_error')
            raise
        error.execution_id = execution_id
        _audit(execution_id, actor_id, mode, 'rollback', supplied, operations, error.code)
        raise error from exc
    except Exception as exc:
        exc.execution_id = execution_id
        if isinstance(exc, ApplyError):
            exc.execution_id = execution_id
        elif isinstance(exc, ComparePayloadError):
            error = ApplyError('invalid_apply_payload', str(exc), exc.status)
            error.execution_id = execution_id
            _audit(execution_id, actor_id, mode, 'refused', supplied, error_code=error.code)
            raise error from exc
        _audit(execution_id, actor_id, mode, 'rollback' if mode == 'commit' else 'refused', supplied,
               operations, exc.code if isinstance(exc, ApplyError) else 'unexpected_server_error')
        raise
    # The business transaction has completed. Post-commit side effects must
    # never turn a successful commit into a response suggesting rollback.
    try:
        _audit(execution_id, actor_id, mode, 'committed' if mode == 'commit' and operations else 'validated', supplied, operations)
    except Exception:
        result['audit_warning'] = 'post_commit_log_unavailable'
    if mode == 'commit' and operations:
        try:
            bump_public_resorts_version()
            for slug in slugs:
                invalidate_station(slug)
        except Exception:
            result['cache_warning'] = 'post_commit_invalidation_failed'
    return result
