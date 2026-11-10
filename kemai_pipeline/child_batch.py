"""Reconcile one document through standard APIs, grouping identical update patches."""
from contextlib import nullcontext
import json
from .lovrabet_adapter import ContractError, _json_default

TABLE_KEYS = {'purchase_document_lines': 'source_line_id',
              'purchase_document_branches': 'source_key',
              'purchase_document_approvals': 'source_key'}
MAX_ROWS = 200
MAX_BYTES = 48000


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=_json_default)


def chunks(rows, wrap):
    batch = []
    for row in rows:
        candidate = batch + [row]
        if len(candidate) > MAX_ROWS or len(encoded(wrap(candidate)).encode()) > MAX_BYTES:
            if not batch:
                raise ContractError('CHILD_BATCH_BYTE_LIMIT')
            yield wrap(batch)
            batch = [row]
            if len(encoded(wrap(batch)).encode()) > MAX_BYTES:
                raise ContractError('CHILD_BATCH_BYTE_LIMIT')
        else:
            batch = candidate
    if batch:
        yield wrap(batch)


def reconcile(client, table, document_id, rows, before, *, measure=None):
    """Caller holds the shared writer lock and supplies a complete scoped pre-read.

    No per-row reads, no SQL, no deletes, and no retries after an unknown write.
    All operations are prepared/validated before the first write. A single complete
    scoped read-back verifies all intended rows, including those considered unchanged.
    """
    if table not in TABLE_KEYS:
        raise ValueError('Unsupported child table')
    key_field = TABLE_KEYS[table]
    code = client.dataset_code(table)
    measure = measure or (lambda phase: nullcontext())
    rows, before = list(rows), list(before)

    def keyed(values, require_id=False):
        mapped, ids = {}, set()
        for row in values:
            if str(row.get('document_id')) != str(document_id):
                raise ContractError('CHILD_SCOPE_MISMATCH')
            if row.get(key_field) is None or isinstance(row.get(key_field), bool):
                raise ContractError('CHILD_KEY_MISSING')
            key = str(row[key_field])
            if key in mapped:
                raise ContractError('DUPLICATE_CHILD_KEY')
            if require_id:
                value = row.get('id')
                if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value):
                    raise ContractError('CHILD_DATABASE_ID_MISSING')
                if str(value) in ids:
                    raise ContractError('DUPLICATE_CHILD_DATABASE_ID')
                ids.add(str(value))
            if 'tenant_id' in row and str(row['tenant_id']) != str(client.config['tenant_id']):
                raise ContractError('CHILD_TENANT_MISMATCH')
            mapped[key] = row
        return mapped

    desired = keyed(rows)
    existing = keyed(before, require_id=True)
    creates, groups = [], {}
    stats = {'existing_rows': len(before), 'desired_rows': len(rows), 'unchanged_rows': 0,
             'created_rows': 0, 'updated_rows': 0, 'update_groups': 0,
             'update_requests': 0, 'create_requests': 0, 'changed_fields': {}}
    for key, row in desired.items():
        if 'id' in row or 'tenant_id' in row:
            raise ContractError('CHILD_INPUT_MUST_USE_BUSINESS_KEYS')
        old = existing.get(key)
        if old is None:
            creates.append(row)
            continue
        patch = {field: value for field, value in row.items()
                 if field not in ('document_id', key_field)
                 and not client._same(old.get(field), value)}
        if not patch:
            stats['unchanged_rows'] += 1
            continue
        for field in patch:
            stats['changed_fields'][field] = stats['changed_fields'].get(field, 0) + 1
        # Exact serialized patches only: different values/fields are never merged.
        signature = encoded(patch)
        group = groups.setdefault(signature, {'patch': patch, 'ids': []})
        group['ids'].append(old['id'])
        stats['updated_rows'] += 1
    stats['created_rows'] = len(creates)
    stats['update_groups'] = len(groups)
    operations = [('batchCreate', payload) for payload in chunks(creates, lambda values: values)]
    for group in groups.values():
        operations.extend(('update', payload) for payload in chunks(group['ids'],
            lambda ids, patch=group['patch']: {'id': ids, **patch}))

    uncertain = None
    for verb, payload in operations:
        field = 'create_requests' if verb == 'batchCreate' else 'update_requests'
        stats[field] += 1
        try:
            with measure('children_batch_create' if verb == 'batchCreate' else 'children_grouped_update'):
                client.invoke('data', verb, '--code', code, '--params', encoded(payload))
        except ContractError as error:
            uncertain = error
            break  # Reconcile first; never execute the remaining writes blindly.
    with measure('children_final_read'):
        saved = keyed(client.read_all(table, where={'document_id': {'$eq': document_id}}), require_id=True)
    for key, row in desired.items():
        actual = saved.get(key)
        if (actual is None or any(not client._same(actual.get(field), value) for field, value in row.items())
                or (key in existing and str(actual['id']) != str(existing[key]['id']))):
            raise ContractError('UPSERT_OUTCOME_UNKNOWN: child batch read-back mismatch; resume and reconcile') from uncertain
    return {'stale': bool(set(saved) - set(desired)), 'stats': stats,
            'unknown_write_reconciled': uncertain is not None}
