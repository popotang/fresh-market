"""Private OpenCLI batch journal. Source-only; no data-center dependencies."""
import hashlib
import json
import re
import subprocess
import time
import uuid
from .artifacts import atomic_json, ArtifactError
from .contracts import digest
from .source import SourceCooling, SourceError
from .source_gate import cooldown_until, gate_dir
from .rate_limit import is_rate_limited, record_limit


def document_details(source, kind, docs, directory):
    if kind not in ('order', 'receipt', 'return') or not 1 <= len(docs) <= 10:
        raise ValueError('Purchase batch must contain 1..10 documents')
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise ArtifactError('Batch journal must be private')
    entities, pending, paths = {}, [], {}
    for doc in docs:
        sid = str(doc['source_document_id'])
        if not re.fullmatch(r'\d{1,32}', sid) or sid in paths:
            raise ValueError('Duplicate or invalid source identity')
        # Unknown versions cannot reuse an earlier invocation's response.
        key = digest([kind, sid, doc.get('source_version'),
                      uuid.uuid4().hex if doc.get('source_version') is None else 'versioned'])
        paths[sid] = (directory / (key+'.json'), key)
        if not paths[sid][0].exists():
            pending.append({'id':sid, 'key':key})

    def recover():
        for sid, (path, key) in paths.items():
            if not path.exists():
                continue
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ArtifactError('Unsafe batch artifact')
            value = json.loads(path.read_text())
            body = value.get('entity_json')
            if (value.get('schema_version') != 1 or value.get('kind') != kind
                    or value.get('id') != sid or value.get('key') != key
                    or not isinstance(body, str)
                    or hashlib.sha256(body.encode()).hexdigest() != value.get('sha256')):
                raise ArtifactError('Batch artifact identity/digest mismatch')
            entity = json.loads(body)
            if not isinstance(entity, dict) or str(entity.get('id')) != sid:
                raise ArtifactError('Batch entity identity mismatch')
            entities[sid] = entity

    recover()  # Corrupt prior evidence fails before another source request.
    metrics = {'requested':len(docs), 'journal_hits':len(entities), 'cli_calls':0,
               'reserved_source_operations':0, 'source_http_requests':None}
    error = None
    if pending:
        if cooldown_until(gate_dir()) > time.time():
            return entities, metrics, SourceCooling('Shared cooldown active')
        if source.operations + len(pending) > source.max_operations:
            raise SourceError('Batch exceeds remaining source operation budget')
        request = directory / ('request-'+uuid.uuid4().hex+'.json')
        atomic_json(request, {'schema_version':1, 'kind':kind, 'items':pending})
        before = set(directory.glob('batch-timing-*.json'))
        source.operations += len(pending)  # Conservative reservation, never count a batch as one source operation.
        metrics.update(cli_calls=1, reserved_source_operations=len(pending))
        started = time.perf_counter()
        try:
            result = source.executor(['opencli','kemai','purchase-detail-batch',
                                      '--request-file',str(request),'-f','json'],
                                     capture_output=True,text=True,timeout=120+30*len(pending))
            if result.returncode:
                if is_rate_limited(result.stderr) or is_rate_limited(result.stdout):
                    # JS gate normally persisted this already. Do not extend an active cooldown.
                    if cooldown_until(gate_dir()) <= time.time():
                        record_limit('purchase-detail-batch','RATE_LIMIT: batch interrupted',wait=False)
                    error = SourceCooling('Batch cooling; completed artifacts retained')
                else:
                    error = RuntimeError('Batch CLI failed; resume retained artifacts')
        except subprocess.TimeoutExpired:
            error = RuntimeError('Batch CLI timeout; inspect retained artifacts before resume')
        except OSError:
            error = RuntimeError('Batch CLI unavailable; resume after fixing runtime')
        finally:
            metrics['cli_wall_seconds'] = time.perf_counter()-started
        reports = sorted(set(directory.glob('batch-timing-*.json'))-before)
        if reports:
            metrics['batch_reports'] = [json.loads(path.read_text()) for path in reports]
            metrics['cli_setup_and_teardown_seconds'] = max(0, metrics['cli_wall_seconds'] -
                sum(r.get('command_seconds', 0) for r in metrics['batch_reports']))
        recover()  # Includes successes before a failed process or lost stdout.
    if error is None and len(entities) != len(docs):
        error = RuntimeError('Incomplete batch artifacts; resume required')
    return entities, metrics, error
