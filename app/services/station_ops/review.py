"""Recompute COMPARE once and review an in-memory action plan. Never APPLY."""
from copy import deepcopy
from collections import defaultdict

from .candidates import ComparePayloadError, MAX_CANDIDATES
from .compare import compare_candidates
from .creation import creation_constraints, create_operation
from .operations import REVIEW_VERSION, apply_plan, canonical_json, relation_operations, scalar_operation

REVIEW_STATUSES = ('no_action', 'pending_review', 'partially_approved', 'approved', 'rejected', 'blocked', 'invalid')


class ReviewPayloadError(ComparePayloadError):
    pass


class ReviewDecisionError(ValueError):
    def __init__(self, issues):
        super().__init__('Decisions do not match the current review plan')
        self.issues = issues


def _parse_review(payload):
    if not isinstance(payload, dict) or set(payload) - {'candidates', 'decisions'} or 'candidates' not in payload:
        raise ReviewPayloadError('Expected candidates and optional decisions; COMPARE results are not accepted')
    candidates = payload['candidates']
    if not isinstance(candidates, list) or not candidates:
        raise ReviewPayloadError('candidates must be a non-empty array')
    if len(candidates) > MAX_CANDIDATES:
        raise ReviewPayloadError(f'Batch exceeds {MAX_CANDIDATES} candidates', 413)
    refs = []
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get('client_ref'), str) or not item['client_ref'].strip() or len(item['client_ref']) > 256:
            raise ReviewPayloadError('REVIEW requires a non-empty client_ref of at most 256 characters for each candidate')
        refs.append(item['client_ref'])
    if len(set(refs)) != len(refs):
        raise ReviewPayloadError('REVIEW requires unique client_ref values')
    ref_set = set(refs)
    decisions = payload.get('decisions', [])
    if not isinstance(decisions, list):
        raise ReviewPayloadError('decisions must be an array')
    parsed = defaultdict(dict)
    for row in decisions:
        if (not isinstance(row, dict) or set(row) != {'client_ref', 'operations'} or
                not isinstance(row['client_ref'], str) or not isinstance(row['operations'], dict)):
            raise ReviewPayloadError('Each decision must contain client_ref and an operations object')
        if row['client_ref'] not in ref_set:
            raise ReviewPayloadError('Decision targets an unknown client_ref')
        for identity, value in row['operations'].items():
            if not isinstance(identity, str) or not identity or value not in ('approved', 'rejected'):
                raise ReviewPayloadError('Decision values must be approved or rejected for an operation_id')
            if identity in parsed[row['client_ref']] and parsed[row['client_ref']][identity] != value:
                raise ReviewPayloadError('Contradictory decisions for the same operation_id')
            parsed[row['client_ref']][identity] = value
    return candidates, parsed


def review_candidates(payload):
    candidates, decisions = _parse_review(payload)
    def builder(compared, prepared, database, context):
        return _build_review(compared, prepared, database, context, decisions)
    return compare_candidates({'candidates': candidates}, result_builder=builder)


def _build_review(compared, prepared, database, context, decisions):
    constraints = creation_constraints(database) if any(row['status'] == 'new' for row in compared['results']) else None
    results = []
    for compared_row, original in zip(compared['results'], prepared):
        row = {key: deepcopy(compared_row[key]) for key in ('client_ref', 'matched_station', 'match_reasons', 'validation', 'review_items')}
        row.update(compare_status=compared_row['status'], operations=[], status='pending_review')
        if compared_row['status'] in ('invalid', 'review_required'):
            row['status'] = 'invalid' if compared_row['status'] == 'invalid' else 'blocked'
        elif compared_row['status'] == 'unchanged':
            row['status'] = 'no_action'
        else:
            creation = None
            if compared_row['status'] == 'new':
                creation, limitations = create_operation(compared_row, original, constraints)
                row['review_items'].extend(limitations)
                if limitations:
                    row['status'] = 'blocked'
                else:
                    row['operations'].append(creation)
            if row['status'] != 'blocked':
                for change in compared_row['changes']:
                    if change['field'] == 'ski_areas':
                        row['operations'].extend(relation_operations(compared_row, change, original, context['areas'],
                                                                     creation['operation_id'] if creation else None))
                    elif not creation:
                        row['operations'].append(scalar_operation(compared_row, change, original))
        results.append(row)
    _block_batch_conflicts(results)
    _apply_decisions(results, decisions)
    for row in results:
        if row['status'] not in ('blocked', 'invalid', 'no_action'):
            values = [op['decision'] for op in row['operations']]
            if all(value == 'approved' for value in values):
                row['status'] = 'approved'
            elif all(value == 'rejected' for value in values):
                row['status'] = 'rejected'
            elif 'approved' in values:
                row['status'] = 'partially_approved'
            else:
                row['status'] = 'pending_review'
    operations = [op for row in results for op in row['operations']]
    summary = {'total_candidates': len(results), **{status: sum(row['status'] == status for row in results) for status in REVIEW_STATUSES},
               **{decision + '_operations': sum(op['decision'] == decision for op in operations) for decision in ('approved', 'pending', 'rejected')}}
    return {**{key: compared[key] for key in ('schema_version', 'compare_version', 'generated_at', 'schema_findings', 'catalog_findings')},
            'review_version': REVIEW_VERSION, 'summary': summary, 'results': results, 'apply_plan': apply_plan(results)}


def _apply_decisions(results, decisions):
    issues, approved_ids = [], set()
    for row in results:
        by_id = {op['operation_id']: op for op in row['operations']}
        for identity, decision in decisions[row['client_ref']].items():
            if row['status'] in ('blocked', 'invalid') and decision == 'approved':
                issues.append({'code': 'approval_on_blocked_candidate', 'client_ref': row['client_ref'], 'operation_id': identity})
            elif identity not in by_id:
                issues.append({'code': 'unknown_or_stale_operation', 'client_ref': row['client_ref'], 'operation_id': identity,
                               'available_operation_ids': sorted(by_id)})
            else:
                by_id[identity]['decision'] = decision
                if decision == 'approved':
                    approved_ids.add(identity)
    for row in results:
        for op in row['operations']:
            if op['decision'] == 'approved' and any(identity not in approved_ids for identity in op.get('depends_on', [])):
                issues.append({'code': 'operation_dependency_not_approved', 'client_ref': row['client_ref'],
                               'operation_id': op['operation_id'], 'depends_on': op['depends_on']})
    if issues:
        raise ReviewDecisionError(issues)


def _block_batch_conflicts(results):
    targets = defaultdict(list)
    for row in results:
        for op in row['operations']:
            if op['operation'] == 'create_station':
                for field in ('id', 'slug'):
                    if op['candidate'].get(field):
                        targets[('create', field, op['candidate'][field])].append((row, None))
            elif op['field'] != 'ski_areas':
                targets[('field', op['target_id'], op['field'])].append((row, canonical_json(op['normalized_candidate'])))
    blocked = defaultdict(set)
    for key, entries in targets.items():
        if len(entries) > 1 and (key[0] == 'create' or len({value for _, value in entries}) > 1):
            for row, _ in entries:
                blocked[row['client_ref']].update(other['client_ref'] for other, _ in entries if other is not row)
    for row in results:
        if row['client_ref'] in blocked:
            row.update(status='blocked', operations=[])
            row['review_items'].append({'code': 'batch_target_conflict', 'client_refs': sorted(blocked[row['client_ref']])})
